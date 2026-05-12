"""Speculative data generation via top-down constraint propagation.

The speculative component walks the Plan top-down — from "I want at least
one output row" backward through each operator — deriving what each table
needs. It produces requirements for BOTH positive and negative branches,
ensuring the generated database can distinguish equivalent from
non-equivalent queries.

Public API::

    from parseval.symbolic.speculate import speculate
    rows_per_table = speculate(plan, instance, alias_map, dialect)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.planner import (
    Aggregate, Filter, Having, Join, Limit, Project, Scan, Sort, SubPlan,
)
from parseval.plan.rex import concrete
from parseval.solver.lowering import (
    ColumnPredicate,
    ColumnUnionFind,
    lower_predicates,
    match_column as _match_col,
    negate_predicate_value,
    resolve_table as _resolve_col_table,
    resolve_table_name as _resolve_tbl,
)


# =============================================================================
# Data structures
# =============================================================================


@dataclass
class TableRequirement:
    """What one table needs to contribute for a specific branch."""
    table: str
    min_rows: int = 1
    fixed_values: Dict[str, Any] = field(default_factory=dict)
    not_null: Set[str] = field(default_factory=set)
    must_null: Set[str] = field(default_factory=set)
    predicates: List[Tuple[str, str, Any]] = field(default_factory=list)
    duplicate_columns: List[str] = field(default_factory=list)


# ColumnUnionFind imported from solver.lowering


@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""
    branch: str
    requirements: Dict[str, TableRequirement] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)

    def require(self, table: str) -> TableRequirement:
        if table not in self.requirements:
            self.requirements[table] = TableRequirement(table=table)
        return self.requirements[table]

    def equate(self, col_a: str, col_b: str) -> None:
        """Declare two columns must have the same value."""
        self.equivalences.union(col_a, col_b)


# =============================================================================
# Propagator: top-down constraint derivation
# =============================================================================


class Propagator:
    """Walk the Plan top-down, deriving table requirements for each branch."""

    def __init__(self, plan: Plan, instance: Instance, alias_map: Dict[str, str], dialect: str):
        self.plan = plan
        self.instance = instance
        self.alias_map = alias_map
        self.dialect = dialect
        self._key_counter = 0

    def _next_key(self) -> str:
        self._key_counter += 1
        return f"k{self._key_counter}"

    def _resolve_table(self, name: str) -> str:
        if not name:
            return ""
        return _resolve_tbl(name, self.instance, self.alias_map)

    def _match_column(self, table: str, col_name: str) -> Optional[str]:
        return _match_col(self.instance, table, col_name)

    def propagate(self) -> List[BranchSpec]:
        """Produce specs for positive + all negative branches."""
        specs = []
        # Positive path.
        pos = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, pos)
        specs.append(pos)
        # Negative branches per decision site.
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                neg = BranchSpec(branch="negative")
                self._propagate_step(self.plan.root, neg, negate_step=step)
                specs.append(neg)
            elif isinstance(step, Join):
                left_un = BranchSpec(branch="left_unmatched")
                self._propagate_unmatched_left(step, left_un)
                specs.append(left_un)
            elif isinstance(step, Having) and step.condition:
                fail = BranchSpec(branch="having_fail")
                self._propagate_step(self.plan.root, fail, negate_step=step)
                specs.append(fail)
        return specs

    def _propagate_step(self, step: Step, spec: BranchSpec, negate_step: Optional[Step] = None):
        """Recursively propagate requirements top-down."""
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            needed = offset + int(limit_val)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Increase min_rows for all tables.
            for req in spec.requirements.values():
                req.min_rows = max(req.min_rows, needed)

        elif isinstance(step, Project):
            # NOT NULL for projected columns + duplicate requirement.
            projected = self._projected_columns(step)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Apply to all tables in spec.
            for table, req in spec.requirements.items():
                for col in projected:
                    matched = self._match_column(table, col)
                    if matched:
                        req.not_null.add(matched)
                # Duplicate: ensure ≥2 rows with same projected values.
                dup_cols = [c for c in projected if self._match_column(table, c)]
                if dup_cols:
                    req.duplicate_columns = dup_cols
                    req.min_rows = max(req.min_rows, 2)

        elif isinstance(step, Sort):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition and step is not negate_step:
                self._extract_predicates(step.condition, spec)
                # HAVING with aggregate: derive min group size.
                min_size = self._extract_min_group_size(step.condition)
                for req in spec.requirements.values():
                    req.min_rows = max(req.min_rows, min_size)

        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # GROUP BY: mark group columns as needing the same value across rows.
            # If the column is already in an equivalence class (from JOIN), that's fine —
            # Union-Find handles it naturally (union with self is a no-op).
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        table = self._resolve_table(col.table or "")
                        matched = self._match_column(table, col.name)
                        if matched:
                            spec.require(table)
                            # Register in union-find (ensures it appears in groups())
                            spec.equivalences.find(f"{table}.{matched}")

        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition:
                if step is negate_step:
                    self._extract_negated_predicates(step.condition, spec)
                else:
                    self._extract_predicates(step.condition, spec)

        elif isinstance(step, Join):
            # Process chain dependencies first.
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Link join keys via equivalence (Union-Find).
            for join_name, join_data in (step.joins or {}).items():
                join_table = self._resolve_table(join_name)
                source_keys = join_data.get("source_key", [])
                join_keys = join_data.get("join_key", [])
                for sk, jk in zip(source_keys, join_keys):
                    sk_table_name = sk.table if hasattr(sk, "table") and sk.table else (step.source_name or step.name)
                    sk_table = self._resolve_table(sk_table_name)
                    sk_col = self._match_column(sk_table, sk.name if hasattr(sk, "name") else str(sk))
                    jk_col = self._match_column(join_table, jk.name if hasattr(jk, "name") else str(jk))
                    if sk_col and jk_col:
                        spec.require(sk_table)
                        spec.require(join_table)
                        spec.equate(f"{sk_table}.{sk_col}", f"{join_table}.{jk_col}")

        elif isinstance(step, Scan):
            table = self._resolve_table(step.name)
            if table in self.instance.tables:
                spec.require(table)
            # For FROM-subquery scans, propagate into the SubPlan's inner plan.
            for sub in step.subplan_dependencies:
                if sub.inner:
                    self._propagate_step(sub.inner, spec, negate_step)

        # Handle SubPlan dependencies.
        for sub in step.subplan_dependencies:
            self._propagate_subplan(sub, spec)

    def _propagate_unmatched_left(self, join_step: Join, spec: BranchSpec):
        """Generate a left-table row with no matching right-table row."""
        source = self._resolve_table(join_step.source_name or join_step.name)
        if source in self.instance.tables:
            spec.require(source)  # Just needs to exist, no shared key.

    def _propagate_subplan(self, sub: SubPlan, spec: BranchSpec):
        """Handle EXISTS/IN subplan correlation."""
        if sub.kind.value == "exists" and sub.correlation:
            for corr_col in sub.correlation:
                outer_table = self._resolve_table(corr_col.table or "")
                matched = self._match_column(outer_table, corr_col.name)
                if matched:
                    spec.require(outer_table)
                    outer_key = f"{outer_table}.{matched}"
                    # Link inner table's correlated column.
                    inner_key = self._find_inner_corr_column(sub, spec)
                    if inner_key:
                        spec.equate(outer_key, inner_key)

    def _find_inner_corr_column(self, sub: SubPlan, spec: BranchSpec) -> Optional[str]:
        """Find the inner plan's correlated column and return its qualified name."""
        stack = [sub.inner]
        while stack:
            step = stack.pop()
            if isinstance(step, Filter) and step.condition:
                for col in step.condition.find_all(exp.Column):
                    inner_table = self._resolve_table(col.table or "")
                    if inner_table in self.instance.tables:
                        matched = self._match_column(inner_table, col.name)
                        if matched:
                            spec.require(inner_table)
                            return f"{inner_table}.{matched}"
            stack.extend(step.chain_dependencies)
        return None

    def _extract_predicates(self, condition: exp.Expression, spec: BranchSpec):
        """Extract value constraints from a predicate using centralized lowering."""
        tables = tuple(spec.requirements.keys()) or tuple(
            v for v in self.alias_map.values() if v in self.instance.tables
        )
        preds, _ = lower_predicates(condition, self.instance, tables, self.alias_map)
        for pred in preds:
            if pred.op == "=":
                spec.require(pred.table).fixed_values[pred.column] = pred.value
            elif pred.op == "is_null":
                spec.require(pred.table).must_null.add(pred.column)
            else:
                spec.require(pred.table).predicates.append((pred.column, pred.op, pred.value))

    def _extract_negated_predicates(self, condition: exp.Expression, spec: BranchSpec):
        """Extract NEGATED constraints for negative branches."""
        tables = tuple(spec.requirements.keys()) or tuple(
            v for v in self.alias_map.values() if v in self.instance.tables
        )
        preds, _ = lower_predicates(condition, self.instance, tables, self.alias_map)
        for pred in preds:
            neg_op, neg_val = negate_predicate_value(pred.op, pred.value)
            if neg_op == "=":
                spec.require(pred.table).fixed_values[pred.column] = neg_val
            else:
                spec.require(pred.table).predicates.append((pred.column, neg_op, neg_val))

    def _extract_min_group_size(self, condition: exp.Expression) -> int:
        """Extract minimum group size from HAVING (e.g., COUNT(*) > 3 → 4)."""
        for node in condition.find_all(exp.GT):
            if node.this.find(exp.Count):
                val = concrete(node.expression)
                if isinstance(val, (int, float)):
                    return int(val) + 1
        for node in condition.find_all(exp.GTE):
            if node.this.find(exp.Count):
                val = concrete(node.expression)
                if isinstance(val, (int, float)):
                    return int(val)
        return 1

    def _projected_columns(self, step: Project) -> List[str]:
        cols = []
        for proj in step.projections:
            if isinstance(proj, exp.Expression):
                for col in proj.find_all(exp.Column):
                    cols.append(col.name)
        return cols



class Resolver:
    """Turn TableRequirements into concrete row values."""

    def __init__(self, instance: Instance, dialect: str = "sqlite"):
        self.instance = instance
        self.dialect = dialect

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        """Produce concrete rows for each table in the spec."""
        # Resolve equivalence classes: one value per group.
        shared_values = self._resolve_equivalences(spec)
        order = self._creation_order(spec)
        result: Dict[str, List[Dict[str, Any]]] = {}

        for table in order:
            if table not in spec.requirements:
                continue
            req = spec.requirements[table]
            rows = self._resolve_table(table, req, shared_values)
            result[table] = rows

        return result

    def _resolve_equivalences(self, spec: BranchSpec) -> Dict[str, Any]:
        """Resolve each equivalence class to a single concrete value."""
        shared: Dict[str, Any] = {}  # "table.col" → value
        groups = spec.equivalences.groups()

        for representative, members in groups.items():
            # Check if any member already has a fixed value.
            fixed_val = None
            for member in members:
                parts = member.split(".", 1)
                if len(parts) == 2:
                    table, col = parts
                    req = spec.requirements.get(table)
                    if req and col in req.fixed_values:
                        fixed_val = req.fixed_values[col]
                        break

            if fixed_val is not None:
                value = fixed_val
            else:
                # Generate a value based on the first member's type.
                value = self._generate_equiv_value(members, spec)

            for member in members:
                shared[member] = value

        return shared

    def _generate_equiv_value(self, members: List[str], spec: BranchSpec) -> Any:
        """Generate a value for an equivalence class."""
        # Use the first member's column type.
        for member in members:
            parts = member.split(".", 1)
            if len(parts) != 2:
                continue
            table, col = parts
            if table not in self.instance.tables:
                continue
            col_type = self.instance.tables[table].get(col, "TEXT")
            type_str = str(col_type).upper()

            # Avoid existing unique values.
            existing: Set[Any] = set()
            if self.instance.is_unique(table, col):
                existing = {s.concrete for s in self.instance.get_column_data(table, col) if s.concrete is not None}

            if "INT" in type_str:
                v = 1
                while v in existing:
                    v += 1
                return v
            elif "TEXT" in type_str or "CHAR" in type_str:
                v = "key_1"
                i = 1
                while v in existing:
                    i += 1
                    v = f"key_{i}"
                return v
            else:
                return 1
        return 1

    def _resolve_table(self, table: str, req: TableRequirement, shared: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        base_row = self._build_row(table, req, shared)
        rows.append(base_row)

        for i in range(1, req.min_rows):
            row = self._build_row(table, req, shared)
            # For duplicate columns: copy values from base row.
            if req.duplicate_columns and i == 1:
                for col in req.duplicate_columns:
                    if col in base_row:
                        row[col] = base_row[col]
            rows.append(row)

        return rows

    def _build_row(self, table: str, req: TableRequirement, shared: Dict[str, Any]) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        # Equivalence class values (JOIN/GROUP BY coordination).
        for col in self.instance.tables.get(table, {}):
            key = f"{table}.{col}"
            if key in shared:
                row[col] = shared[key]
        # Fixed values override equivalences (WHERE constraints are more specific).
        row.update(req.fixed_values)
        # Predicates (only for columns not already set).
        for col, op, value in req.predicates:
            if col not in row:
                row[col] = self._satisfy(op, value)
        # Must NULL.
        for col in req.must_null:
            row[col] = None
        return row

    def _satisfy(self, op: str, value: Any) -> Any:
        """Generate a value satisfying the predicate."""
        if op == ">" and isinstance(value, (int, float)):
            return value + 1
        if op == ">=" and isinstance(value, (int, float)):
            return value
        if op == "<" and isinstance(value, (int, float)):
            return value - 1
        if op == "<=" and isinstance(value, (int, float)):
            return value
        if op == "=":
            return value
        return value



    def _creation_order(self, spec: BranchSpec) -> List[str]:
        tables = list(spec.requirements.keys())
        deps: Dict[str, Set[str]] = {t: set() for t in tables}
        for table in tables:
            for fk in self.instance.get_foreign_key(table):
                ref = fk.args.get("reference")
                if ref:
                    ref_table = ref.find(exp.Table)
                    if ref_table and normalize_name(ref_table.name) in deps:
                        deps[table].add(normalize_name(ref_table.name))
        ordered: List[str] = []
        ready = [t for t in tables if not deps[t]]
        while ready:
            t = ready.pop(0)
            ordered.append(t)
            for other in tables:
                if t in deps.get(other, set()):
                    deps[other].discard(t)
                    if not deps[other] and other not in ordered:
                        ready.append(other)
        for t in tables:
            if t not in ordered:
                ordered.append(t)
        return ordered


# =============================================================================
# Top-level API
# =============================================================================


def speculate(
    plan: Plan,
    instance: Instance,
    alias_map: Dict[str, str],
    dialect: str = "sqlite",
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve → list of (branch_name, rows_per_table).

    Returns one entry per branch (positive + negatives). The engine
    materializes each one.
    """
    propagator = Propagator(plan, instance, alias_map, dialect)
    resolver = Resolver(instance, dialect)
    branch_specs = propagator.propagate()

    results = []
    for spec in branch_specs:
        if spec.requirements:
            rows = resolver.resolve(spec)
            results.append((spec.branch, rows))
    return results


# Also keep backward-compatible names used by engine.
def build_spec(plan, instance, *, alias_map, target_outcome="positive", negate_atom=None):
    """Backward-compatible wrapper."""
    propagator = Propagator(plan, instance, alias_map, dialect="sqlite")
    if target_outcome == "positive":
        specs = propagator.propagate()
        return specs[0] if specs else BranchSpec(branch="positive")
    return BranchSpec(branch=target_outcome)


def resolve_spec(spec, instance, dialect="sqlite"):
    """Backward-compatible wrapper."""
    resolver = Resolver(instance, dialect)
    rows = resolver.resolve(spec)
    # Flatten to {table: first_row_values}
    return {table: row_list[0] if row_list else {} for table, row_list in rows.items()}


# Keep SharedKey and UNSET for backward compat with engine imports.
@dataclass
class SharedKey:
    key_id: str

UNSET = object()

SpeculativeSpec = BranchSpec


__all__ = [
    "BranchSpec",
    "Propagator",
    "Resolver",
    "SharedKey",
    "SpeculativeSpec",
    "TableRequirement",
    "UNSET",
    "build_spec",
    "resolve_spec",
    "speculate",
]
