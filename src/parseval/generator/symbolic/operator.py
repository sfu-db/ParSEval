from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, TYPE_CHECKING

from sqlglot import exp

from parseval.plan.context import DerivedSchema, Row
from parseval.plan.rex import Symbol
from parseval.solver.types import SolverVar, Problem
from parseval.solver.api import Solver
from parseval.domain.exceptions import ConstraintViolationError, UniqueConflictError
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Limit,
    Plan,
    Projection,
    Sort,
    Step,
    TableScan,
    Union,
    SubqueryAlias,
    Values,
    EmptyRelation,
    Unnest,
    Repartition,
    Distinct,
    Window,
)
from parseval.plan.rex import Environment, Variable, concrete
from parseval.solver.types import SolverVar
from parseval.generator.coverage import (
    SemanticTarget,
    _is_not_null_filter,
)

from . import values as v

if TYPE_CHECKING:
    from parseval.instance import Instance


def _step_name(step: Step) -> str:
    return step.name.name if step.name else type(step).__name__


def _database_check_constraints_for_solver(
    instance: Instance,
    table: exp.Table,
    sv_map: Mapping[str, SolverVar],
    exact_columns: Set[str],
) -> List[exp.Expression]:
    table_schema = instance.database_constraints(table)
    constraints: List[exp.Expression] = []
    available = set(sv_map)
    for check in table_schema.checks:
        if not check.supported:
            continue
        referenced = {column.name for column in check.referenced_columns}
        if not referenced or not referenced <= available:
            continue
        if exact_columns and not referenced.intersection(exact_columns):
            continue
        rewritten = deepcopy(check.expression)
        for col in list(rewritten.find_all(exp.Column)):
            if isinstance(col.this, exp.Identifier) and col.this.name in sv_map:
                col.replace(sv_map[col.this.name])
        constraints.append(rewritten)
    for group in table_schema.uniqueness_groups():
        names = tuple(column.name for column in group)
        if not set(names) <= available:
            continue
        if exact_columns and not set(names).intersection(exact_columns):
            continue
        for row in instance.get_rows(table_schema.table):
            values = v._row_value_dict(row)
            existing = [values.get(column) for column in group]
            if any(value is None for value in existing):
                continue
            constraints.append(_unique_non_collision_constraint(sv_map, names, existing))
    for fk in table_schema.foreign_keys:
        names = tuple(column.name for column in fk.source_columns)
        if len(names) != 1 or not set(names) <= available:
            continue
        if exact_columns and not set(names).intersection(exact_columns):
            continue
        target_values = []
        target_column = fk.target_columns[0]
        for parent_row in instance.get_rows(fk.target_table):
            value = v._row_value_dict(parent_row).get(target_column)
            if value is not None:
                target_values.append(value)
        if target_values:
            constraints.append(
                exp.In(
                    this=sv_map[names[0]],
                    expressions=[
                        _literal_for_value(value)
                        for value in dict.fromkeys(target_values)
                    ],
                )
            )
    return constraints


def _unique_non_collision_constraint(
    sv_map: Mapping[str, SolverVar],
    names: Tuple[str, ...],
    existing: List[Any],
) -> exp.Expression:
    atoms = [
        exp.NEQ(this=sv_map[name], expression=_literal_for_value(value))
        for name, value in zip(names, existing)
    ]
    if len(atoms) == 1:
        return atoms[0]
    expr = atoms[0]
    for atom in atoms[1:]:
        expr = exp.Or(this=expr, expression=atom)
    return expr


def _literal_for_value(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    return exp.Literal(this=str(value), is_string=isinstance(value, str))


# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------

class EncodeStep:
    """Base class for a single-step concrete-enrichment operator.

    Each operator corresponds to one :class:`Step` in the plan DAG and
    implements :meth:`forward` to ensure the :class:`Instance` has rows
    that cover the operator's semantics (both passing and failing).
    """

    def __init__(self, step: Step, instance: Optional[Instance] = None) -> None:
        self.step = step
        self.instance = instance

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        raise NotImplementedError

    @staticmethod
    def decompose_conjuncts(expr: exp.Expression) -> List[exp.Expression]:
        if isinstance(expr, exp.And):
            return (
                EncodeStep.decompose_conjuncts(expr.left)
                + EncodeStep.decompose_conjuncts(expr.right)
            )
        return [expr]

    @staticmethod
    def decompose_disjuncts(expr: exp.Expression) -> List[exp.Expression]:
        if isinstance(expr, exp.Or):
            return (
                EncodeStep.decompose_disjuncts(expr.left)
                + EncodeStep.decompose_disjuncts(expr.right)
            )
        return [expr]

    @staticmethod
    def referenced_columns(expr: exp.Expression) -> Set[exp.Identifier]:
        return {
            col.this
            for col in expr.find_all(exp.Column)
            if isinstance(col.this, exp.Identifier)
        }

    def _resolve_table(self, ds: DerivedSchema) -> exp.Table:
        if hasattr(ds, "_table"):
            return ds._table
        if ds.rows:
            for row in ds.rows:
                for part in row.rowid:
                    if isinstance(part, str) and not part.startswith("rowid_"):
                        return exp.to_table(part)
        raise ValueError("Cannot determine table resolution from DerivedSchema without _table")

    def _resolve_table_from_target(self, target: SemanticTarget) -> exp.Table:
        scans = self._leaf_table_scans(target.step)
        if len(scans) == 1:
            return scans[0].table
        raise ValueError("Cannot find TableScan dependency")

    def _leaf_table_scans(self, step: Step) -> Tuple[TableScan, ...]:
        if isinstance(step, TableScan):
            return (step,)
        scans: List[TableScan] = []
        for dependency in step.dependencies:
            scans.extend(self._leaf_table_scans(dependency))
        return tuple(scans)

    def _mock_plan(self) -> Plan:
        return Plan(self.step, sql="", dialect="sqlite")

    @staticmethod
    def _solve_row(
        instance: Instance,
        table: exp.Table,
        constraints: List[exp.Expression],
        *,
        dialect: str = "sqlite",
        timeout_ms: int = 2000,
    ) -> Optional[Dict[str, Any]]:
        """Use the Solver to find concrete column values satisfying *constraints*."""
        columns = instance.column_names(table)
        table_node = instance.resolve_table(table)
        sv_row: Dict[str, Any] = {}
        solver_constraints: List[exp.Expression] = []
        sv_map: Dict[str, SolverVar] = {}

        constrained_cols: Set[str] = set()
        for atom in constraints:
            for col in atom.find_all(exp.Column):
                if isinstance(col.this, exp.Identifier):
                    constrained_cols.add(col.this.name)

        for col_name in columns:
            col_ident = instance.resolve_column(table_node, col_name)
            dtype = instance.get_column_type(table_node, col_ident)
            sv = SolverVar(key=f"gen.{col_name}", dtype=dtype)
            sv_map[col_name] = sv
            if not instance.nullable(table_node, col_ident):
                solver_constraints.append(exp.Not(this=exp.Is(this=sv, expression=exp.Null())))
            if col_name not in constrained_cols and not instance.is_unique(table_node, col_ident):
                domain = v.existing_domain(instance, table_node, col_ident)
                if domain:
                    unique_vals = list(dict.fromkeys(domain))
                    val_exprs = [exp.Literal(this=str(v), is_string=isinstance(v, str)) for v in unique_vals]
                    solver_constraints.append(exp.In(this=sv, expressions=val_exprs))

        for atom in constraints:
            rewritten = deepcopy(atom)
            for sv_name, sv in sv_map.items():
                for col in list(rewritten.find_all(exp.Column)):
                    if isinstance(col.this, exp.Identifier) and col.this.name == sv_name:
                        col.replace(sv)
            solver_constraints.append(rewritten)
        solver_constraints.extend(
            _database_check_constraints_for_solver(
                instance,
                table_node,
                sv_map,
                constrained_cols,
            )
        )

        problem = Problem(constraints=solver_constraints)
        solver = Solver(dialect=dialect, timeout_ms=timeout_ms)
        result = solver.solve(problem)
        if not result.sat:
            return None
        for col_name, sv in sv_map.items():
            if sv in result.assignments:
                col_ident = instance.resolve_column(table_node, col_name)
                if (
                    col_name not in constrained_cols
                    and instance.is_unique(table_node, col_ident)
                ):
                    continue
                sv_row[col_name] = result.assignments[sv]
        return sv_row


# ------------------------------------------------------------------
# Scan
# ------------------------------------------------------------------

class ScanEncodeStep(EncodeStep):
    """Load concrete rows from :class:`Instance`."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        scan: TableScan = self.step
        table = scan.table
        col_keys: List[exp.Column] = [
            p for p in scan.scan_projections if isinstance(p, exp.Column)
        ]

        rows: List[Row] = []
        if self.instance is not None:
            for existing in self.instance.get_rows(table):
                selected = {}
                for col_key in col_keys:
                    if isinstance(col_key.this, exp.Identifier):
                        val = existing[col_key.this]
                        if isinstance(val, Variable):
                            selected[col_key] = val.concrete
                        elif isinstance(val, SolverVar):
                            selected[col_key] = None
                        else:
                            selected[col_key] = val
                if selected:
                    rows.append(Row(this=(table.name, existing.rowid), columns=selected))

        datatypes = {}
        for col_key in col_keys:
            if self.instance is not None:
                datatypes[col_key] = self.instance.get_column_type(table, col_key)

        ds = DerivedSchema(
            columns=tuple(col_keys),
            rows=rows,
            datatypes=datatypes,
        )
        ds._table = table
        return ds


# ------------------------------------------------------------------
# Filter
# ------------------------------------------------------------------

class FilterEncodeStep(EncodeStep):
    """Ensure Instance has rows that satisfy, fail, and null-evaluate the filter."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        fs: Filter = self.step
        if fs.condition is None or self.instance is None:
            return child

        kept_rows: List[Row] = []
        cols = child.columns
        if getattr(child, "_table", None) is None:
            for row in child.rows:
                if concrete(fs.condition, Environment.from_row(row)) is True:
                    kept_rows.append(row)
            return child.with_rows(kept_rows)

        table = self._resolve_table(child)
        for instance_row in self.instance.get_rows(table):
            env = Environment.from_row(instance_row)
            if fs.condition is None or concrete(fs.condition, env) is True:
                selected = {}
                for ident in cols:
                    try:
                        raw = instance_row[ident]
                        selected[ident] = raw.concrete if isinstance(raw, Symbol) else raw
                    except KeyError:
                        pass
                kept_rows.append(Row(this=instance_row.rowid, columns=selected))

        result = child.with_rows(kept_rows)
        result._table = getattr(child, '_table', None)
        return result

    def _ensure_filter_true(self, target: SemanticTarget) -> None:
        table = self._resolve_table_from_target(target)
        conjuncts = self.decompose_conjuncts(target.expression)
        or_branches: Dict[int, List[exp.Expression]] = {}
        for i, c in enumerate(conjuncts):
            if isinstance(c, exp.Or):
                or_branches[i] = self.decompose_disjuncts(c)

        if not v.has_row_satisfying(self.instance, table, conjuncts):
            gen_row = self._solve_row(self.instance, table, conjuncts)
            if gen_row is not None:
                try:
                    self.instance.create_rows({table: [gen_row]})
                except (ConstraintViolationError, UniqueConflictError):
                    pass

        for ci, branches in or_branches.items():
            for branch in branches:
                branch_atoms = [conjuncts[j] for j in range(len(conjuncts)) if j != ci]
                if not v.has_row_satisfying(self.instance, table, branch_atoms + [branch]):
                    gen_row = self._solve_row(self.instance, table, branch_atoms + [branch])
                    if gen_row is not None:
                        try:
                            self.instance.create_rows({table: [gen_row]})
                        except (ConstraintViolationError, UniqueConflictError):
                            pass

    def _ensure_filter_false(self, target: SemanticTarget) -> None:
        table = self._resolve_table_from_target(target)
        conjuncts = self.decompose_conjuncts(target.expression)
        for i, atom in enumerate(conjuncts):
            if isinstance(atom, exp.Or):
                continue
            if _is_not_null_filter(atom):
                continue
            if v.has_row_violating(self.instance, table, conjuncts, i):
                continue
            others = [c for j, c in enumerate(conjuncts) if j != i]
            negated = exp.Not(this=deepcopy(atom))
            gen_row = self._solve_row(self.instance, table, others + [negated])
            if gen_row is not None:
                try:
                    self.instance.create_rows({table: [gen_row]})
                except (ConstraintViolationError, UniqueConflictError):
                    pass

    def _ensure_filter_null(self, target: SemanticTarget) -> None:
        table = self._resolve_table_from_target(target)
        if v.has_row_with_null_outcome(self.instance, table, target.expression):
            return
        conjuncts = self.decompose_conjuncts(target.expression)
        candidates = sorted(
            enumerate(conjuncts),
            key=lambda item: 0 if isinstance(item[1], exp.EQ) else 1,
        )
        for atom_index, atom in candidates:
            other_atoms = [
                deepcopy(other)
                for index, other in enumerate(conjuncts)
                if index != atom_index
            ]
            for col in atom.find_all(exp.Column):
                if isinstance(col.this, exp.Identifier):
                    col_ident = self.instance.resolve_column(table, col.this.name)
                    if self.instance.nullable(table, col_ident):
                        null_atom = exp.Is(this=deepcopy(col), expression=exp.Null())
                        gen_row = self._solve_row(
                            self.instance,
                            table,
                            other_atoms + [null_atom],
                        )
                        if gen_row is not None:
                            gen_row[col.this.name] = None
                            try:
                                self.instance.create_rows({table: [gen_row]})
                            except (ConstraintViolationError, UniqueConflictError):
                                pass
                            return

# ------------------------------------------------------------------
# Projection
# ------------------------------------------------------------------

class ProjectEncodeStep(EncodeStep):
    """Subset columns to those referenced in projections."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        proj_step: Projection = self.step

        col_map: Dict[exp.Column, exp.Expression] = {}
        for proj in proj_step.projections:
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Column):
                if isinstance(proj, exp.Alias):
                    alias_ident = exp.Identifier(
                        this=proj.alias,
                        quoted=inner.this.quoted if inner.this else False,
                    )
                    col_map[inner] = alias_ident
                else:
                    col_map[inner] = inner.copy()

        new_rows: List[Row] = []
        for row in child.rows:
            new_columns = {}
            for src_col, dst_ident in col_map.items():
                try:
                    new_columns[dst_ident] = row[src_col]
                except KeyError:
                    pass
            if new_columns:
                new_rows.append(Row(this=row.rowid, columns=new_columns))

        out_cols = tuple(col_map.values())
        result = child.with_rows(new_rows, columns=out_cols)
        result._table = getattr(child, '_table', None)
        return result


# ------------------------------------------------------------------
# Join
# ------------------------------------------------------------------

class JoinEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        join_step: Join = self.step
        child_map: Dict[Step, DerivedSchema] = {}
        for dep, ds in zip(join_step.dependencies, children):
            child_map[dep] = ds
        left_ds = child_map.get(join_step.left, children[0])
        right_ds = child_map.get(join_step.right, children[-1])

        if self.instance is None:
            return self._build_join_output(left_ds, right_ds, join_step)

        result = self._build_join_output(left_ds, right_ds, join_step)
        result._table = getattr(left_ds, '_table', None)
        return result

    def _ensure_join_match(
        self,
        left_table: exp.Table,
        right_table: exp.Table,
        join_step: Join,
        left_ds: DerivedSchema,
        right_ds: DerivedSchema,
    ) -> None:
        if v.has_matching_pair(
            self.instance, left_table, right_table,
            join_step.on_keys, join_step.condition,
        ):
            return
        equality_constraints = [
            exp.EQ(this=deepcopy(lexpr), expression=deepcopy(rexpr))
            for lexpr, rexpr in join_step.on_keys
        ]
        all_conjuncts = equality_constraints + ([join_step.condition] if join_step.condition else [])

        # Try reusing an existing left row
        for lrow in self.instance.get_rows(left_table):
            ldict = v._row_value_dict(lrow)
            right_constraints = []
            for lexpr, rexpr in join_step.on_keys:
                if isinstance(rexpr, exp.Column) and isinstance(lexpr, exp.Column) and isinstance(lexpr.this, exp.Identifier):
                    lname = lexpr.this.name
                    if lname in ldict:
                        right_constraints.append(
                            exp.EQ(this=deepcopy(rexpr), expression=exp.Literal(this=str(ldict[lname]), is_string=isinstance(ldict[lname], str)))
                        )
            if join_step.condition:
                right_constraints.append(join_step.condition)
            rdict = self._solve_row(self.instance, right_table, right_constraints)
            if rdict is not None:
                self.instance.create_rows({left_table: [ldict], right_table: [rdict]})
                return

        # Both fresh — solve for left, then right with ON keys fixed
        ldict = self._solve_row(self.instance, left_table, [])
        if ldict is not None:
            right_constraints = []
            for lexpr, rexpr in join_step.on_keys:
                if isinstance(rexpr, exp.Column) and isinstance(lexpr, exp.Column) and isinstance(lexpr.this, exp.Identifier):
                    lname = lexpr.this.name
                    if lname in ldict:
                        right_constraints.append(
                            exp.EQ(this=deepcopy(rexpr), expression=exp.Literal(this=str(ldict[lname]), is_string=isinstance(ldict[lname], str)))
                        )
            if join_step.condition:
                right_constraints.append(join_step.condition)
            rdict = self._solve_row(self.instance, right_table, right_constraints)
            if rdict is not None:
                self.instance.create_rows({left_table: [ldict], right_table: [rdict]})

    def _ensure_join_no_match(
        self,
        left_table: exp.Table,
        right_table: exp.Table,
        join_step: Join,
        left_ds: DerivedSchema,
        right_ds: DerivedSchema,
    ) -> None:
        if left_ds.rows and not v.has_non_matching_row(
            self.instance, left_table, right_table, join_step.on_keys,
        ):
            for orow in self.instance.get_rows(right_table):
                odict = v._row_value_dict(orow)
                not_matches = []
                for lexpr, rexpr in join_step.on_keys:
                    if isinstance(rexpr, exp.Column) and isinstance(rexpr.this, exp.Identifier):
                        rname = rexpr.this.name
                        if rname in odict:
                            not_matches.append(
                                exp.NEQ(this=deepcopy(lexpr), expression=exp.Literal(this=str(odict[rname]), is_string=isinstance(odict[rname], str)))
                            )
                row = self._solve_row(self.instance, left_table, not_matches)
                if row is not None:
                    self.instance.create_rows({left_table: [row]})
                    break

        if right_ds.rows and not v.has_non_matching_row(
            self.instance, right_table, left_table,
            [(r, l) for l, r in join_step.on_keys],
        ):
            for orow in self.instance.get_rows(left_table):
                odict = v._row_value_dict(orow)
                not_matches = []
                for rexpr, lexpr in [(r, l) for l, r in join_step.on_keys]:
                    if isinstance(lexpr, exp.Column) and isinstance(lexpr.this, exp.Identifier):
                        lname = lexpr.this.name
                        if lname in odict:
                            not_matches.append(
                                exp.NEQ(this=deepcopy(rexpr), expression=exp.Literal(this=str(odict[lname]), is_string=isinstance(odict[lname], str)))
                            )
                row = self._solve_row(self.instance, right_table, not_matches)
                if row is not None:
                    self.instance.create_rows({right_table: [row]})
                    break

    def _ensure_preserved_unmatched(
        self,
        left_table: exp.Table,
        right_table: exp.Table,
        join_step: Join,
        left_ds: DerivedSchema,
        right_ds: DerivedSchema,
    ) -> None:
        jt = join_step.join_type.upper()
        preserved = left_table if jt == "LEFT" else right_table
        null_padded = right_table if jt == "LEFT" else left_table
        if v.has_non_matching_row(self.instance, preserved, null_padded, join_step.on_keys):
            return
        for orow in self.instance.get_rows(null_padded):
            odict = v._row_value_dict(orow)
            not_matches = []
            for lexpr, rexpr in join_step.on_keys:
                if isinstance(rexpr, exp.Column) and isinstance(rexpr.this, exp.Identifier):
                    rname = rexpr.this.name
                    if rname in odict:
                        not_matches.append(
                            exp.NEQ(this=deepcopy(lexpr), expression=exp.Literal(this=str(odict[rname]), is_string=isinstance(odict[rname], str)))
                        )
            row = self._solve_row(self.instance, preserved, not_matches)
            if row is not None:
                self.instance.create_rows({preserved: [row]})
                return

    @staticmethod
    def _build_join_output(
        left_ds: DerivedSchema,
        right_ds: DerivedSchema,
        join_step: Join,
    ) -> DerivedSchema:
        out_cols = tuple(left_ds.columns) + tuple(right_ds.columns)

        output_rows: List[Row] = []

        for lrow in left_ds.rows:
            ldict = v._row_value_dict(lrow) if hasattr(lrow, 'column_values') else {}
            for rrow in right_ds.rows:
                rdict = v._row_value_dict(rrow) if hasattr(rrow, 'column_values') else {}
                merged = {**ldict, **rdict}
                env = Environment(row=merged)
                ok = True
                for lexpr, rexpr in join_step.on_keys:
                    lv = concrete(lexpr, env)
                    rv = concrete(rexpr, env)
                    if lv is None or rv is None or lv != rv:
                        ok = False
                        break
                if ok and join_step.condition is not None:
                    if concrete(join_step.condition, env) is not True:
                        ok = False
                if not ok:
                    continue
                out_row = Row(
                    this=(_step_name(join_step), lrow.rowid, rrow.rowid),
                    columns={ident: merged.get(ident, None)
                             for ident in out_cols},
                )
                output_rows.append(out_row)

        return DerivedSchema(columns=out_cols, rows=output_rows)

# ------------------------------------------------------------------
# Stub operators (passthrough)
# ------------------------------------------------------------------

class SubqueryAliasEncodeStep(EncodeStep):
    """Remap Column keys from child's table name to the alias."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        alias_step: SubqueryAlias = self.step
        alias_name = alias_step.alias.name if alias_step.alias else ""

        if not alias_name:
            return child

        new_rows: List[Row] = []
        for row in child.rows:
            new_columns = {}
            for key, val in row.column_values.items():
                if isinstance(key, exp.Column) and key.table:
                    new_key = exp.Column(
                        this=exp.Identifier(this=key.this.name if key.this else ""),
                        table=exp.Identifier(this=alias_name, quoted=_quoted(key.table)),
                    )
                    new_columns[new_key] = val
                else:
                    col_name = key.name if isinstance(key, exp.Identifier) else str(key)
                    new_key = exp.Column(
                        this=exp.Identifier(this=col_name, quoted=getattr(key, 'quoted', False)),
                        table=exp.Identifier(this=alias_name),
                    )
                    new_columns[new_key] = val
            new_rows.append(Row(this=row.rowid, columns=new_columns))

        new_cols = tuple(
            exp.Column(
                this=exp.Identifier(this=c.name if isinstance(c, exp.Identifier) else (c.this.name if isinstance(c, exp.Column) and c.this else "")),
                table=exp.Identifier(this=alias_name),
            )
            for c in child.columns
        )

        result = child.with_rows(new_rows, columns=new_cols)
        result._table = getattr(child, '_table', None)
        return result


class AggregateEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Aggregate = self.step
        group_exprs = tuple(step.group or ())
        aggregations = tuple(step.aggregations or ())

        grouped: Dict[Tuple[Any, ...], List[Row]] = {}
        for row in child.rows:
            key = tuple(_expr_value(expr, row) for expr in group_exprs)
            grouped.setdefault(key, []).append(row)
        if not grouped and not group_exprs:
            grouped[()] = []

        out_cols: List[Any] = []
        out_cols.extend(group_exprs)
        out_cols.extend(_aggregate_key(aggregate) for aggregate in aggregations)
        out_rows: List[Row] = []

        for group_index, (group_key, rows) in enumerate(grouped.items()):
            values: Dict[Any, Any] = {}
            for expr, value in zip(group_exprs, group_key):
                values[expr] = value
            for aggregate in aggregations:
                values[_aggregate_key(aggregate)] = _aggregate_value(aggregate, rows)
            rowids = tuple(row.rowid for row in rows)
            out_rows.append(
                Row(
                    this=(_step_name(step), str(group_index), *rowids),
                    columns=values,
                )
            )

        result = child.with_rows(out_rows, columns=tuple(out_cols))
        result.obligations.append(
            {"kind": "aggregate", "target": "groups", "count": len(out_rows)}
        )
        return result


class SortEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Sort = self.step

        rows = list(child.rows)
        for key in reversed(step.key or []):
            expr = key.this if isinstance(key, exp.Ordered) else key
            desc = isinstance(key, exp.Ordered) and bool(key.args.get("desc"))
            rows.sort(
                key=lambda row: _sort_key(_expr_value(expr, row)),
                reverse=desc,
            )
        if step.fetch is not None:
            rows = rows[: step.fetch]
        result = child.with_rows(rows)
        result.obligations.append(
            {"kind": "sort", "target": "ordered", "count": len(rows)}
        )
        return result


class LimitEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Limit = self.step
        offset = step.offset or 0
        stop = None if step.fetch is None else offset + step.fetch
        rows = list(child.rows)[offset:stop]
        result = child.with_rows(rows)
        result.obligations.append(
            {
                "kind": "limit",
                "target": "window",
                "offset": offset,
                "fetch": step.fetch,
                "count": len(rows),
            }
        )
        return result


class DistinctEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        seen: Set[Tuple[Any, ...]] = set()
        rows: List[Row] = []
        for row in child.rows:
            key = tuple(_cell_value(row[column]) for column in child.columns)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        result = child.with_rows(rows)
        result.obligations.append(
            {"kind": "distinct", "target": "duplicate_eliminated", "count": len(rows)}
        )
        return result


class UnionEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: Union = self.step
        columns = _union_columns(children)
        rows: List[Row] = []
        seen: Set[Tuple[Any, ...]] = set()
        for child in children:
            for row in child.rows:
                values = _row_values_by_position(row)
                if len(values) != len(columns):
                    raise ValueError(
                        f"Union row width does not match output columns: {len(values)} != {len(columns)}"
                    )
                key = tuple(_cell_value(value) for value in values)
                if not step.is_all and key in seen:
                    continue
                seen.add(key)
                rows.append(_row_with_columns(row, columns, values))
        result = (children[0] if children else DerivedSchema(columns=columns)).with_rows(
            rows,
            columns=columns,
        )
        result.obligations.append({"kind": "union", "target": "combined", "count": len(rows)})
        return result


def _union_columns(children: Tuple[DerivedSchema, ...]) -> Tuple[Any, ...]:
    for child in children:
        if child.columns:
            return child.columns
    for child in children:
        for row in child.rows:
            return row.columns
    return ()


def _row_values_by_position(row: Row) -> Tuple[Any, ...]:
    return tuple(row.values())


def _row_with_columns(row: Row, columns: Tuple[Any, ...], values: Tuple[Any, ...]) -> Row:
    if row.columns == columns:
        return row
    return Row(this=row.rowid, columns=dict(zip(columns, values)))


class ValuesEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: Values = self.step
        width = max((len(row) for row in step.values), default=0)
        columns = tuple(exp.to_identifier(f"column{i + 1}") for i in range(width))
        rows = [
            Row(
                this=(_step_name(step), str(index)),
                columns={
                    columns[col_index]: concrete(value, Environment())
                    for col_index, value in enumerate(values)
                },
            )
            for index, values in enumerate(step.values)
        ]
        return DerivedSchema(columns=columns, rows=rows)


class EmptyRelationEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: EmptyRelation = self.step
        rows = [Row(this=(_step_name(step), "0"), columns={})] if step.produce_one_row else []
        return DerivedSchema(columns=(), rows=rows)


class RepartitionEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        child.obligations.append(
            {
                "kind": "repartition",
                "target": "preserved",
                "scheme": self.step.partitioning_scheme,
            }
        )
        return child


class WindowEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        child.obligations.append(
            {
                "kind": "window",
                "target": "passthrough",
                "count": len(self.step.window_exprs),
            }
        )
        return child


class UnnestEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        columns = tuple(self.step.columns)
        return DerivedSchema(
            columns=columns,
            rows=[],
            obligations=[
                {
                    "kind": "unnest",
                    "target": "unsupported",
                    "status": "unknown",
                }
            ],
        )


def _quoted(value: Any) -> bool:
    return bool(getattr(value, "quoted", False))


def _row_mapping(row: Row) -> Dict[Any, Any]:
    return {key: _cell_value(value) for key, value in row.column_values.items()}


def _cell_value(value: Any) -> Any:
    return value.concrete if isinstance(value, Symbol) else value


def _expr_value(expr: exp.Expression, row: Row) -> Any:
    try:
        return concrete(expr, Environment.from_row(_row_mapping(row)))
    except Exception:
        try:
            return row[expr]
        except KeyError:
            return None


def _sort_key(value: Any) -> Tuple[int, Any]:
    return (0, None) if value is None else (1, value)


def _aggregate_key(aggregate: exp.Expression) -> exp.Column:
    return exp.Column(
        this=exp.Identifier(
            this=_aggregate_name(aggregate),
            quoted=True,
        )
    )


def _aggregate_name(aggregate: exp.Expression) -> str:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, exp.Count):
        source = expression.this
        if source is None or isinstance(source, exp.Star):
            return "count(Int64(1))"
        return f"count({_aggregate_arg_name(source)})"
    if isinstance(expression, exp.Avg):
        return f"avg({_aggregate_arg_name(expression.this)})"
    if isinstance(expression, exp.Sum):
        return f"sum({_aggregate_arg_name(expression.this)})"
    if isinstance(expression, exp.Min):
        return f"min({_aggregate_arg_name(expression.this)})"
    if isinstance(expression, exp.Max):
        return f"max({_aggregate_arg_name(expression.this)})"
    return expression.alias_or_name or expression.sql(dialect="sqlite")


def _aggregate_arg_name(expr: exp.Expression | None) -> str:
    if expr is None:
        return ""
    while isinstance(expr, exp.Cast):
        expr = expr.this
    if isinstance(expr, exp.Distinct):
        return ", ".join(_aggregate_arg_name(item) for item in expr.expressions)
    return expr.sql(dialect="sqlite")


def _aggregate_value(aggregate: exp.Expression, rows: List[Row]) -> Any:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, exp.Count):
        source = expression.this
        if source is None or isinstance(source, exp.Star):
            return len(rows)
        values = _aggregate_inputs(source, rows)
        if isinstance(source, exp.Distinct):
            return len({value for value in values if value is not None})
        return sum(1 for value in values if value is not None)
    if isinstance(expression, exp.Avg):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else sum(values) / len(values)
    if isinstance(expression, exp.Sum):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else sum(values)
    if isinstance(expression, exp.Min):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else min(values)
    if isinstance(expression, exp.Max):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else max(values)
    return None


def _aggregate_inputs(expr: exp.Expression | None, rows: List[Row]) -> List[Any]:
    if isinstance(expr, exp.Distinct):
        if len(expr.expressions) != 1:
            return []
        expr = expr.expressions[0]
    if expr is None:
        return []
    return [_expr_value(expr, row) for row in rows]


# ------------------------------------------------------------------
# Pipeline orchestrator
# ------------------------------------------------------------------

class EncodePipeline:
    """Orchestrates the concrete-enrichment execution of a query plan.

    Walks the plan DAG bottom-up (leaves → root), dispatching each
    :class:`Step` to its registered operator class.  Each operator
    ensures the :class:`Instance` has rows that cover the operator's
    semantics (both passing and failing).
    """

    _DEFAULT_REGISTRY: Dict[type, type] = {
        TableScan: ScanEncodeStep,
        Filter: FilterEncodeStep,
        Projection: ProjectEncodeStep,
        Join: JoinEncodeStep,
        Aggregate: AggregateEncodeStep,
        Sort: SortEncodeStep,
        Limit: LimitEncodeStep,
        Union: UnionEncodeStep,
        SubqueryAlias: SubqueryAliasEncodeStep,
        Values: ValuesEncodeStep,
        EmptyRelation: EmptyRelationEncodeStep,
        Unnest: UnnestEncodeStep,
        Repartition: RepartitionEncodeStep,
        Distinct: DistinctEncodeStep,
        Window: WindowEncodeStep,
    }

    def __init__(
        self,
        plan: Plan,
        instance: Optional[Instance] = None,
    ) -> None:
        self.plan = plan
        self.instance = instance
        self._operator_registry: Dict[type, type] = dict(self._DEFAULT_REGISTRY)

    def register_operator(self, step_type: type, operator_class: type) -> None:
        self._operator_registry[step_type] = operator_class

    def _build_operator(self, step: Step) -> EncodeStep:
        for step_type, op_cls in self._operator_registry.items():
            if isinstance(step, step_type):
                return op_cls(step, instance=self.instance)
        raise ValueError(f"No operator registered for step type {type(step).__name__}")

    def _process_subqueries(self, step: Step) -> Dict[Step, DerivedSchema]:
        results: Dict[Step, DerivedSchema] = {}

        def _collect_expressions(node: Step) -> List[exp.Expression]:
            exprs: List[exp.Expression] = []
            if isinstance(node, Filter) and node.condition is not None:
                exprs.append(node.condition)
            if isinstance(node, Projection):
                exprs.extend(node.projections)
            if isinstance(node, Join):
                if node.condition is not None:
                    exprs.append(node.condition)
                for l, r in node.on_keys:
                    exprs.append(l)
                    exprs.append(r)
            return exprs

        for expr in _collect_expressions(step):
            for subq in list(expr.find_all(exp.Subquery)):
                inner_root: Step = subq.this
                if inner_root in results:
                    continue
                inner_plan = Plan(inner_root, sql="<subquery>", dialect=self.plan.dialect)
                inner_pipeline = EncodePipeline(inner_plan, instance=self.instance)
                results[inner_root] = inner_pipeline.forward()
        return results

    def forward(self) -> DerivedSchema:
        outputs: Dict[Step, DerivedSchema] = {}
        finished: Set[Step] = set()
        queue: Set[Step] = set(self.plan.leaves)

        while queue:
            node = queue.pop()
            children = [outputs[d] for d in node.dependencies]
            op = self._build_operator(node)

            subq_results = self._process_subqueries(node)
            if subq_results:
                children.extend(subq_results.values())

            outputs[node] = op.forward(*children)
            finished.add(node)

            for dep in node.dependents:
                if all(d in outputs for d in dep.dependencies):
                    queue.add(dep)

            for dep in node.dependencies:
                if all(d in finished for d in dep.dependents):
                    outputs.pop(dep, None)

        return outputs[self.plan.root]
