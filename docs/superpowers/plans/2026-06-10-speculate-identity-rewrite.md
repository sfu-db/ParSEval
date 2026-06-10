# Speculate Identity Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `speculate.py` as a clean-slate implementation using the identity-based solver interface (`SolverVar`, `RelationId`, `ColumnId`).

**Architecture:** Every `exp.Column` carries `SolverVar` metadata from creation. Single global solving path. No string-based resolution, no self-join special-casing, no temporal special-casing, no per-table solving.

**Tech Stack:** Python, sqlglot, parseval.solver, parseval.identity

**Spec:** `docs/superpowers/specs/2026-06-10-speculate-identity-rewrite-design.md`

**File:** `src/parseval/symbolic/speculate.py` (complete rewrite)

---

### Task 1: Foundation — Imports, Helpers, Data Structures

**Files:**
- Rewrite: `src/parseval/symbolic/speculate.py`

Write the complete foundation layer: module docstring, imports, helper functions, and data structures. This is the bottom of the file that everything else builds on.

- [ ] **Step 1: Write the module header and imports**

```python
"""Speculative data generation via top-down constraint propagation.

The speculative component walks the Plan top-down — from "I want at least
one output row" backward through each operator — deriving what each table
needs. It produces requirements for BOTH positive and negative branches,
ensuring the generated database can distinguish equivalent from
non-equivalent queries.

Public API::

    from parseval.symbolic.speculate import speculate
    rows_per_table = speculate(plan, instance, dialect)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.dtype import DataType, TypeFamily, type_family
from parseval.helper import normalize_name
from parseval.identity import (
    ColumnId,
    ColumnKind,
    RelationId,
    RelationKind,
    column_id,
    column_identity,
    identifier_name,
    physical_column,
    relation_id,
)
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.helper import to_literal
from parseval.plan.planner import (
    Aggregate,
    Filter,
    Having,
    Join,
    Limit,
    Project,
    Scan,
    SetOperation,
    Sort,
    SubPlan,
)
from parseval.plan.rex import Environment, column_meta, concrete, negate_predicate
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var, solver_var
from parseval.solver.types import col_type

from .evaluator import PlanEvaluator
from .types import BranchTree, BranchType

logger = logging.getLogger("parseval.speculate")
```

- [ ] **Step 2: Write schema lookup helpers**

```python
def _table_name(relation: RelationId) -> str:
    return relation.name.normalized if relation.name else ""


def _lookup_col_type(instance: Instance, relation: RelationId, col_name: str) -> Optional[str]:
    """Look up column type with case-insensitive fallback."""
    table = _table_name(relation)
    schema = instance.tables.get(table)
    if not schema:
        return None
    dtype = schema.get(normalize_name(col_name))
    if dtype:
        return dtype
    lower = col_name.lower()
    for schema_col, schema_dtype in schema.items():
        if schema_col.lower() == lower:
            return schema_dtype
    return None


def _column_type_family(
    instance: Instance,
    relation: RelationId,
    col_name: str,
    default: TypeFamily = TypeFamily.TEXT,
) -> TypeFamily:
    col_type_str = _lookup_col_type(instance, relation, col_name)
    if not col_type_str:
        return default
    try:
        return type_family(DataType.build(col_type_str))
    except Exception:
        return default


def _match_column(instance: Instance, relation: RelationId, col_name: str) -> Optional[str]:
    """Find the canonical column name in the instance (case-insensitive)."""
    table = _table_name(relation)
    if table not in instance.tables:
        return None
    lower = col_name.lower()
    return next((s for s in instance.tables[table] if s.lower() == lower), None)
```

- [ ] **Step 3: Write identity-aware column creation helpers**

```python
def _relation_for_table(instance: Instance, name: str) -> RelationId:
    normalized = normalize_name(name)
    try:
        return instance.table_id(normalized)
    except (KeyError, Exception):
        return relation_id(RelationKind.TABLE, identifier_name(normalized))


def _solver_column(
    instance: Instance,
    table: str,
    col_name: str,
    row_scope: str | None = None,
) -> exp.Column:
    """Create a Column annotated with SolverVar + type from the instance schema."""
    rel = _relation_for_table(instance, table)
    col_id = column_id(ColumnKind.PHYSICAL, identifier_name(col_name), rel)
    var = SolverVar(column_id=col_id, relation_id=rel, row_scope=row_scope)
    col = exp.column(col_name, table)
    set_solver_var(col, var)
    col_type_str = _lookup_col_type(instance, rel, col_name)
    if col_type_str:
        try:
            col.type = DataType.build(col_type_str)
        except Exception:
            pass
    return col


def _ensure_solver_var(col: exp.Column, instance: Instance) -> None:
    """Ensure a Column has SolverVar metadata. Reads identity from planner annotations."""
    if solver_var(col) is not None:
        return
    col_id = column_identity(col)
    if col_id is None or col_id.relation is None:
        return
    var = SolverVar(column_id=col_id, relation_id=col_id.relation)
    set_solver_var(col, var)
    if col_type(col) is None:
        table = col_id.relation.name.normalized if col_id.relation.name else ""
        matched = col_id.name.normalized
        if table and matched:
            _annotate_col_type(col, instance, col_id.relation, matched)


def _annotate_col_type(
    col_node: exp.Column,
    instance: Instance,
    relation: RelationId,
    col_name: str,
) -> None:
    col_type_str = _lookup_col_type(instance, relation, col_name)
    if col_type_str:
        try:
            col_node.type = DataType.build(col_type_str)
        except Exception:
            pass
```

- [ ] **Step 4: Write constraint helper functions**

```python
def _make_is_not_null(col_node: exp.Column) -> exp.Is:
    return exp.Is(this=col_node, expression=exp.Not(this=exp.Null()))


def _make_is_null(col_node: exp.Column) -> exp.Is:
    return exp.Is(this=col_node, expression=exp.Null())


def _has_is_not_null(constraints: List[exp.Expression], col_name: str) -> bool:
    for expr in constraints:
        if isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Not) and isinstance(expr.expression.this, exp.Null):
            for col in expr.find_all(exp.Column):
                if col.name == col_name:
                    return True
    return False


def _has_is_null(constraints: List[exp.Expression], col_name: str) -> bool:
    for expr in constraints:
        if isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Null):
            for col in expr.find_all(exp.Column):
                if col.name == col_name:
                    return True
    return False


def _has_equality_constraint(constraints: List[exp.Expression], col_name: str) -> bool:
    for expr in constraints:
        if isinstance(expr, exp.EQ):
            left = expr.this
            right = expr.expression
            if isinstance(left, exp.Column) and left.name == col_name:
                return True
            if isinstance(right, exp.Column) and right.name == col_name:
                return True
    return False


def _extract_fixed_values(constraints: List[exp.Expression]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for expr in constraints:
        if not isinstance(expr, exp.EQ):
            continue
        left, right = expr.this, expr.expression
        if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
            val = concrete(right)
            if val is not None:
                values[left.name] = val
        elif isinstance(right, exp.Column) and not isinstance(left, exp.Column):
            val = concrete(left)
            if val is not None:
                values[right.name] = val
    return values
```

- [ ] **Step 5: Write data structures**

```python
@dataclass(frozen=True)
class RowBinding:
    """Transient mapping from a solver table key to one physical witness row."""
    relation: RelationId
    row: int

    @property
    def table(self) -> str:
        return self.relation.name.normalized if self.relation.name else ""

    @property
    def alias(self) -> Optional[str]:
        return self.relation.alias.normalized if self.relation.alias else None


def _solver_table_key(binding: RowBinding) -> str:
    alias = normalize_name(binding.alias or binding.table)
    table = normalize_name(binding.table)
    return f"{table}__{alias}__r{binding.row}"


class ColumnUnionFind:
    """Union-Find for tracking column equivalence classes (JOIN, GROUP BY)."""

    def __init__(self):
        self._parent: Dict[ColumnId, ColumnId] = {}
        self._rank: Dict[ColumnId, int] = {}

    def find(self, x: ColumnId) -> ColumnId:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: ColumnId, y: ColumnId) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            self._parent[rx] = ry
        elif self._rank[rx] > self._rank[ry]:
            self._parent[ry] = rx
        else:
            self._parent[ry] = rx
            self._rank[rx] += 1

    def same(self, x: ColumnId, y: ColumnId) -> bool:
        return self.find(x) == self.find(y)

    def groups(self) -> Dict[ColumnId, List[ColumnId]]:
        result: Dict[ColumnId, List[ColumnId]] = {}
        for x in self._parent:
            rep = self.find(x)
            result.setdefault(rep, []).append(x)
        return result


@dataclass
class TableConstraint:
    """Constraints on what one table needs for a specific branch."""
    relation: RelationId
    constraints: List[exp.Expression] = field(default_factory=list)
    min_rows: int = 1
    duplicate_columns: List[ColumnId] = field(default_factory=list)
    group_key_columns: List[ColumnId] = field(default_factory=list)
    boundary_rows: List[Dict[ColumnId, Any]] = field(default_factory=list)

    @property
    def table(self) -> str:
        return self.relation.name.normalized if self.relation.name else ""

    @property
    def alias(self) -> Optional[str]:
        return self.relation.alias.normalized if self.relation.alias else None


@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""
    branch: str
    requirements: Dict[RelationId, TableConstraint] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)
    deferred: List[exp.Expression] = field(default_factory=list)

    def require(self, relation: RelationId) -> TableConstraint:
        if relation not in self.requirements:
            self.requirements[relation] = TableConstraint(relation=relation)
        return self.requirements[relation]

    def equate(self, col_a: ColumnId, col_b: ColumnId) -> None:
        self.equivalences.union(col_a, col_b)
```

- [ ] **Step 6: Write SpeculateConfig**

```python
@dataclass
class SpeculateConfig:
    """Configuration for speculative data generation."""
    positive: int = 1
    negative: int = 1
    null: int = 1
    left_unmatched: int = 1
    right_unmatched: int = 1
    having_fail: int = 1
    case_else: int = 1
    boundary: int = 1

    @classmethod
    def gold_non_empty(cls) -> SpeculateConfig:
        return cls(
            positive=1, negative=0, null=0, left_unmatched=0,
            right_unmatched=0, having_fail=0, case_else=1, boundary=0,
        )

    @classmethod
    def full_coverage(cls) -> SpeculateConfig:
        return cls()

    @classmethod
    def from_thresholds(cls, thresholds) -> SpeculateConfig:
        from .types import CoverageThresholds
        if not isinstance(thresholds, CoverageThresholds):
            return cls.full_coverage()
        positive = 1 if any([
            thresholds.atom_true > 0, thresholds.filter_true > 0,
            thresholds.join_match > 0, thresholds.having_pass > 0,
            thresholds.case_arm_taken > 0, thresholds.exists_true > 0,
            thresholds.exists_false > 0, thresholds.in_match > 0,
            thresholds.in_no_match > 0, thresholds.group_single > 0,
            thresholds.group_multi > 0, thresholds.distinct_unique > 0,
            thresholds.distinct_duplicate > 0,
        ]) else 0
        negative = 1 if any([thresholds.atom_false > 0, thresholds.filter_false > 0]) else 0
        null = 1 if thresholds.atom_null > 0 else 0
        left_unmatched = 1 if thresholds.join_no_match > 0 else 0
        right_unmatched = 1 if thresholds.join_no_match > 0 else 0
        having_fail = 1 if thresholds.having_fail > 0 else 0
        case_else = 1 if thresholds.case_arm_skipped > 0 else 0
        boundary = 1 if positive > 0 else 0
        return cls(
            positive=positive, negative=negative, null=null,
            left_unmatched=left_unmatched, right_unmatched=right_unmatched,
            having_fail=having_fail, case_else=case_else, boundary=boundary,
        )

    def should_generate(self, branch_type: str) -> bool:
        mapping = {
            "positive": self.positive, "negative": self.negative,
            "null": self.null, "left_unmatched": self.left_unmatched,
            "right_unmatched": self.right_unmatched, "having_fail": self.having_fail,
            "case_else": self.case_else, "boundary": self.boundary,
        }
        return mapping.get(branch_type, 0) > 0
```

- [ ] **Step 7: Verify imports resolve**

Run: `python -c "from parseval.symbolic.speculate import BranchSpec, TableConstraint, SpeculateConfig, ColumnUnionFind, RowBinding"`
Expected: No error

- [ ] **Step 8: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): write foundation layer with identity-aware helpers"
```

---

### Task 2: Propagator

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Write the complete `Propagator` class. This walks the Plan top-down, producing `BranchSpec` objects for each branch type. Every column created uses `_solver_column` or `_ensure_solver_var`.

- [ ] **Step 1: Write Propagator class header and __init__**

```python
_COMPARISON_NODES = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)


class Propagator:
    """Walk the Plan top-down, deriving table requirements for each branch."""

    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        dialect: str,
        config: Optional[SpeculateConfig] = None,
    ):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.config = config or SpeculateConfig.gold_non_empty()
        self._is_gold_mode = (
            self.config.negative == 0
            and self.config.null == 0
            and self.config.left_unmatched == 0
            and self.config.right_unmatched == 0
            and self.config.having_fail == 0
        )
```

- [ ] **Step 2: Write `propagate()` method**

```python
    def propagate(self) -> List[BranchSpec]:
        specs = []

        # Positive path.
        if self.config.positive > 0:
            try:
                pos = BranchSpec(branch="positive")
                self._propagate_step(self.plan.root, pos)
                if self.config.boundary > 0:
                    self._collect_boundary_values(pos)
                self._add_schema_constraints(pos)
                self._annotate_column_types(pos)
                specs.append(pos)
            except Exception as exc:
                logger.debug("positive spec propagation failed: %s", exc)

        # Negative branches per decision site.
        if self.config.negative > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Filter) and step.condition:
                        conjuncts = self._split_conjuncts(step.condition)
                        for idx in range(len(conjuncts)):
                            neg = BranchSpec(branch=f"negative_c{idx}")
                            self._propagate_step(self.plan.root, neg, negate_step=step, negate_conjunct=idx)
                            self._add_schema_constraints(neg)
                            self._annotate_column_types(neg)
                            specs.append(neg)
                except Exception as exc:
                    logger.debug("negative spec propagation failed: %s", exc)

        # Unmatched join branches.
        if self.config.left_unmatched > 0 or self.config.right_unmatched > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Join):
                        if self.config.left_unmatched > 0:
                            left_un = BranchSpec(branch="left_unmatched")
                            self._propagate_unmatched_left(step, left_un)
                            self._add_schema_constraints(left_un)
                            self._annotate_column_types(left_un)
                            specs.append(left_un)
                        if self.config.right_unmatched > 0:
                            for join_name in (step.joins or {}):
                                right_un = BranchSpec(branch=f"right_unmatched_{join_name}")
                                self._propagate_unmatched_right(step, join_name, right_un)
                                self._add_schema_constraints(right_un)
                                self._annotate_column_types(right_un)
                                specs.append(right_un)
                except Exception as exc:
                    logger.debug("unmatched join propagation failed: %s", exc)

        # Having fail branches.
        if self.config.having_fail > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Having) and step.condition:
                        fail = BranchSpec(branch="having_fail")
                        self._propagate_step(self.plan.root, fail, negate_step=step)
                        self._add_schema_constraints(fail)
                        self._annotate_column_types(fail)
                        specs.append(fail)
                except Exception as exc:
                    logger.debug("having_fail propagation failed: %s", exc)

        # Null branches.
        if self.config.null > 0:
            try:
                null_targets = self._collect_null_target_columns(
                    specs[0] if specs else BranchSpec(branch="positive"),
                )
                if null_targets:
                    for table, cols in null_targets.items():
                        for col_name in cols:
                            null_spec = BranchSpec(branch=f"null_{table}.{col_name}")
                            self._propagate_step(self.plan.root, null_spec)
                            self._apply_single_null_override(null_spec, table, col_name)
                            self._add_schema_constraints(null_spec)
                            self._annotate_column_types(null_spec)
                            specs.append(null_spec)
            except Exception as exc:
                logger.debug("null branch propagation failed: %s", exc)

        # CASE WHEN branches.
        if self.config.case_else > 0:
            try:
                for case_idx, when_conditions in enumerate(self._collect_case_when_conditions()):
                    case_spec = BranchSpec(branch=f"case_else_{case_idx}")
                    self._propagate_step(self.plan.root, case_spec)
                    for cond in when_conditions:
                        negated = negate_predicate(cond.copy())
                        self._store_expression(negated, case_spec)
                    self._add_schema_constraints(case_spec)
                    self._annotate_column_types(case_spec)
                    specs.append(case_spec)
            except Exception as exc:
                logger.debug("CASE WHEN propagation failed: %s", exc)

        return specs
```

- [ ] **Step 3: Write `_propagate_step` (recursive step propagation)**

This is the core method. Each step type derives requirements differently.

```python
    def _propagate_step(
        self, step: Step, spec: BranchSpec,
        negate_step: Optional[Step] = None, negate_conjunct: int = 0,
    ):
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            needed = (offset + 1 if int(limit_val) > 0 else 0) if self._is_gold_mode else (offset + int(limit_val))
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Apply min_rows to the driving table.
            for dep in step.chain_dependencies:
                if isinstance(dep, (Scan, Join)):
                    rel = self._relation_for_step(dep)
                    if rel and rel in spec.requirements:
                        spec.requirements[rel].min_rows = max(spec.requirements[rel].min_rows, needed)

        elif isinstance(step, Project):
            projected = self._projected_columns(step)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            for table_rel, tc in spec.requirements.items():
                table = _table_name(table_rel)
                for col in projected:
                    matched = _match_column(self.instance, table_rel, col)
                    if matched and not _has_is_not_null(tc.constraints, matched):
                        tc.constraints.append(_make_is_not_null(_solver_column(self.instance, table, matched)))
                dup_cols = [c for c in projected if _match_column(self.instance, table_rel, c)]
                if step.distinct and dup_cols:
                    tc.duplicate_columns = dup_cols
                    tc.min_rows = max(tc.min_rows, 2)

        elif isinstance(step, Sort):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.condition and step is not negate_step:
                if self._is_gold_mode:
                    for scalar_cond in self._gold_having_scalar_constraints(step.condition):
                        self._store_expression(scalar_cond, spec)
                else:
                    self._store_expression(step.condition, spec)
                counted_table = self._find_counted_table(step.condition)
                min_size = self._extract_min_group_size(step.condition)
                if counted_table:
                    counted_rel = _relation_for_table(self.instance, counted_table)
                    if counted_rel in spec.requirements:
                        spec.requirements[counted_rel].min_rows = max(spec.requirements[counted_rel].min_rows, min_size)
                else:
                    for req in spec.requirements.values():
                        req.min_rows = max(req.min_rows, min_size)
                self._extract_having_value_constraints(step.condition, spec, min_size)

        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        col_id = column_identity(col)
                        if col_id is not None and col_id.relation is not None:
                            rel = col_id.relation
                            matched = col_id.name.normalized
                        else:
                            rel = _relation_for_table(self.instance, col.table or "")
                            matched = _match_column(self.instance, rel, col.name)
                        if matched:
                            req = spec.require(rel)
                            c_id = column_id(ColumnKind.PHYSICAL, identifier_name(matched), rel)
                            spec.equivalences.find(c_id)
                            if c_id not in req.group_key_columns:
                                req.group_key_columns.append(c_id)
            if not self._is_gold_mode:
                for agg_expr in step.aggregations:
                    self._add_aggregate_null_constraints(agg_expr, spec)

        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.condition:
                if step is negate_step:
                    conjuncts = self._split_conjuncts(step.condition)
                    if len(conjuncts) > 1:
                        for idx, conjunct in enumerate(conjuncts):
                            if idx == negate_conjunct:
                                self._store_expression(negate_predicate(conjunct.copy()), spec)
                            else:
                                self._store_expression(conjunct, spec)
                    else:
                        self._store_expression(negate_predicate(step.condition.copy()), spec)
                else:
                    self._store_expression(step.condition, spec)
                self._extract_column_equalities(step.condition, spec)
                for atom in self._iter_scalar_subquery_atoms(step.condition):
                    spec.deferred.append(atom)

        elif isinstance(step, Join):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            for join_name, join_data in (step.joins or {}).items():
                join_rel = _relation_for_table(self.instance, join_name)
                source_keys = join_data.get("source_key", [])
                join_keys = join_data.get("join_key", [])
                for sk, jk in zip(source_keys, join_keys):
                    sk_id = column_identity(sk) if isinstance(sk, exp.Column) else None
                    jk_id = column_identity(jk) if isinstance(jk, exp.Column) else None
                    sk_rel = sk_id.relation if sk_id and sk_id.relation else _relation_for_table(self.instance, sk.table or step.source_name or step.name)
                    jk_rel = jk_id.relation if jk_id and jk_id.relation else join_rel
                    sk_matched = _match_column(self.instance, sk_rel, sk.name if hasattr(sk, "name") else str(sk))
                    jk_matched = _match_column(self.instance, jk_rel, jk.name if hasattr(jk, "name") else str(jk))
                    if sk_matched and jk_matched:
                        sk_col_id = column_id(ColumnKind.PHYSICAL, identifier_name(sk_matched), sk_rel)
                        jk_col_id = column_id(ColumnKind.PHYSICAL, identifier_name(jk_matched), jk_rel)
                        spec.require(sk_rel)
                        spec.require(jk_rel)
                        spec.equate(sk_col_id, jk_col_id)
                        sk_table = _table_name(sk_rel)
                        jk_table = _table_name(jk_rel)
                        eq_expr = exp.EQ(
                            this=_solver_column(self.instance, sk_table, sk_matched),
                            expression=_solver_column(self.instance, jk_table, jk_matched),
                        )
                        spec.requirements[sk_rel].constraints.append(eq_expr)
                        spec.requirements[jk_rel].constraints.append(eq_expr)
                        req_jk = spec.require(jk_rel)
                        if jk_col_id not in req_jk.group_key_columns:
                            req_jk.group_key_columns.append(jk_col_id)

        elif isinstance(step, SetOperation):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)

        elif isinstance(step, Scan):
            rel_id = getattr(step, "relation_id", None)
            if rel_id is not None:
                spec.require(rel_id)
            elif step.source and isinstance(step.source, exp.Table):
                rel = _relation_for_table(self.instance, step.source.name)
                if rel.name and _table_name(rel) in self.instance.tables:
                    spec.require(rel)
            elif step.name:
                rel = _relation_for_table(self.instance, step.name)
                if rel.name and _table_name(rel) in self.instance.tables:
                    spec.require(rel)
            for sub in step.subplan_dependencies:
                if sub.inner:
                    self._propagate_step(sub.inner, spec, negate_step, negate_conjunct)

        for sub in step.subplan_dependencies:
            self._propagate_subplan(sub, spec, parent_condition=getattr(step, "condition", None))
```

- [ ] **Step 4: Write expression storage and column resolution**

```python
    def _relation_for_step(self, step: Step) -> Optional[RelationId]:
        if isinstance(step, Scan):
            rel_id = getattr(step, "relation_id", None)
            if rel_id is not None:
                return rel_id
            if step.source and isinstance(step.source, exp.Table):
                return _relation_for_table(self.instance, step.source.name)
        return None

    def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
        conjuncts = self._split_conjuncts(expr)
        for conjunct in conjuncts:
            if conjunct.find(exp.Exists) or conjunct.find(exp.Subquery):
                spec.deferred.append(conjunct.copy())
                continue
            resolved = self._resolve_columns(conjunct.copy())
            for col in resolved.find_all(exp.Column):
                _ensure_solver_var(col, self.instance)
            table_rel = self._find_table_for_expr(resolved)
            if table_rel:
                tc = spec.require(table_rel)
                tc.constraints.append(resolved)

    def _split_conjuncts(self, expr: exp.Expression) -> List[exp.Expression]:
        parts: List[exp.Expression] = []
        if isinstance(expr, exp.And):
            parts.extend(self._split_conjuncts(expr.left))
            parts.extend(self._split_conjuncts(expr.right))
        elif isinstance(expr, exp.Paren):
            parts.extend(self._split_conjuncts(expr.this))
        else:
            parts.append(expr)
        return parts

    def _find_table_for_expr(self, expr: exp.Expression) -> Optional[RelationId]:
        if isinstance(expr, exp.EQ):
            left = expr.this
            if isinstance(left, exp.Column):
                col_id = column_identity(left)
                if col_id is not None and col_id.relation is not None:
                    return col_id.relation
                table = normalize_name(left.table or "")
                if table and table in self.instance.tables:
                    return _relation_for_table(self.instance, table)
        for col in expr.find_all(exp.Column):
            col_id = column_identity(col)
            if col_id is not None and col_id.relation is not None:
                return col_id.relation
            table = normalize_name(col.table or "")
            if table and table in self.instance.tables:
                return _relation_for_table(self.instance, table)
        return None

    def _resolve_columns(self, expr: exp.Expression) -> exp.Expression:
        for col in expr.find_all(exp.Column):
            col_id = column_identity(col)
            if col_id is not None and col_id.relation is not None:
                physical = col_id.relation.name.normalized
                if physical and physical != normalize_name(col.table or ""):
                    col.set("table", exp.to_identifier(physical))
        return expr
```

- [ ] **Step 5: Write schema constraints**

```python
    def _add_schema_constraints(self, spec: BranchSpec):
        for table_rel, tc in list(spec.requirements.items()):
            table = tc.table
            if not table:
                continue
            # NOT NULL
            for col_name in self.instance.tables.get(table, {}):
                if not self.instance.nullable(table_rel, physical_column(col_name, table_rel)):
                    if _has_is_null(tc.constraints, col_name):
                        continue
                    if not _has_is_not_null(tc.constraints, col_name):
                        tc.constraints.append(_make_is_not_null(_solver_column(self.instance, table, col_name)))
            # UNIQUE exclusion
            existing_rows = self.instance.get_rows(table_rel)
            if existing_rows:
                for col_name in self.instance.tables.get(table, {}):
                    if self.instance.is_unique(table_rel, physical_column(col_name, table_rel)):
                        existing_vals = []
                        for row in existing_rows:
                            sym = row.get(table, col_name)
                            if sym is not None and sym.concrete is not None:
                                existing_vals.append(sym.concrete)
                        if existing_vals:
                            col_node = _solver_column(self.instance, table, col_name)
                            literals = [
                                exp.Literal.number(v) if isinstance(v, (int, float))
                                else exp.Literal.string(str(v))
                                for v in existing_vals
                            ]
                            tc.constraints.append(exp.Not(this=exp.In(this=col_node, expressions=literals)))
            # FK constraints
            for fk in self.instance.get_foreign_key(table_rel):
                ref = fk.args.get("reference")
                if not ref:
                    continue
                ref_table_node = ref.find(exp.Table)
                if not ref_table_node:
                    continue
                ref_table = normalize_name(ref_table_node.name)
                fk_cols = [identifier.name for identifier in fk.expressions]
                if not fk_cols:
                    continue
                ref_rel = _relation_for_table(self.instance, ref_table)
                parent_rows = self.instance.get_rows(ref_rel)
                if parent_rows:
                    ref_col_name = self.instance.resolve_fk_ref_column(fk)
                    if ref_col_name:
                        parent_vals = []
                        for row in parent_rows:
                            sym = row.get(ref_table, ref_col_name)
                            if sym is not None and sym.concrete is not None:
                                parent_vals.append(sym.concrete)
                        if parent_vals:
                            col_node = _solver_column(self.instance, table, fk_cols[0])
                            literals = [
                                exp.Literal.number(v) if isinstance(v, (int, float))
                                else exp.Literal.string(str(v))
                                for v in parent_vals
                            ]
                            tc.constraints.append(exp.In(this=col_node, expressions=literals))
```

- [ ] **Step 6: Write NULL branch, boundary, aggregate, HAVING, join, subplan, CASE WHEN helpers**

Write each of these as methods on the Propagator class. The logic is the same as the existing code but uses `_solver_column` instead of `_make_typed_column` and `column_identity(col)` instead of `_relation_for_column`/`_match_column`.

Key methods to write:
- `_collect_null_target_columns`
- `_apply_single_null_override`
- `_collect_boundary_values`
- `_extract_boundary_from_conjunct`
- `_annotate_column_types`
- `_add_aggregate_null_constraints`
- `_extract_column_equalities`
- `_extract_having_value_constraints`
- `_extract_min_group_size`
- `_propagate_unmatched_left`
- `_propagate_unmatched_right`
- `_propagate_subplan` (+ correlation helpers)
- `_collect_case_when_conditions`
- `_projected_columns`
- `_gold_having_scalar_constraints`

Each method follows the same pattern: use `column_identity(col)` to get identity, use `_solver_column(instance, table, name)` to create annotated columns.

- [ ] **Step 7: Verify Propagator can be instantiated**

Run: `python -c "from parseval.symbolic.speculate import Propagator"`
Expected: No error

- [ ] **Step 8: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): write Propagator with identity-based column creation"
```

---

### Task 3: Resolver + Solving Helpers

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Write the `Resolver` class and all solving/extraction helpers.

- [ ] **Step 1: Write Resolver class**

```python
class Resolver:
    """Turn BranchSpec into concrete row values via global constraint solving."""

    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        dialect: str = "sqlite",
        solver=None,
    ):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.solver = solver

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        constraint, row_bindings = self._build_global_constraint(spec)
        result = self.solver.solve(constraint)
        if result.sat:
            rows = _rows_from_solver_result(result.assignments, row_bindings, self.instance)
        else:
            logger.warning("Solver failed for spec=%s reason=%s", spec.branch, result.reason)
            rows = _fallback_rows(spec, self.instance, row_bindings)
        if not rows:
            rows = _fallback_rows(spec, self.instance, row_bindings)
        if not rows:
            return {}
        rows = _complete_gold_rows(rows, row_bindings, spec, self.instance)
        _satisfy_gold_scalar_subqueries(spec, self.plan, rows, self.instance, self.dialect)
        try:
            _materialize_rows(self.instance, rows)
            return rows
        except Exception as exc:
            logger.debug("materialization failed for spec=%s: %s", spec.branch, exc)
            return {}
```

- [ ] **Step 2: Write `_build_global_constraint`**

```python
    def _build_global_constraint(
        self, spec: BranchSpec,
    ) -> Tuple[SolverConstraint, Dict[str, RowBinding]]:
        row_bindings = _build_gold_row_bindings(spec)
        constraints: List[exp.Expression] = []
        variables: Dict[SolverVar, DataType] = {}

        for table_key, req in spec.requirements.items():
            req_bindings = _bindings_for_requirement(table_key, req, row_bindings)
            if not req_bindings:
                continue
            for constraint in req.constraints:
                if constraint.find(exp.Subquery):
                    continue
                if (isinstance(constraint, exp.EQ)
                        and isinstance(constraint.this, exp.Column)
                        and isinstance(constraint.expression, exp.Column)):
                    continue
                for binding in req_bindings:
                    rewritten = _rewrite_constraint_for_binding(constraint, binding, self.instance)
                    if rewritten is not None:
                        constraints.append(rewritten)
                        _collect_solver_vars(rewritten, variables)
            # Boundary rows
            for b_idx, boundary in enumerate(req.boundary_rows):
                binding = RowBinding(relation=req.relation, row=1000 + b_idx)
                row_bindings[_solver_table_key(binding)] = binding
                for col_id_obj, val in boundary.items():
                    col_name = col_id_obj.name.normalized
                    col = _solver_column(self.instance, req.table, col_name, row_scope=f"r{binding.row}")
                    constraints.append(exp.EQ(this=col, expression=to_literal(val)))
                    variables[solver_var(col)] = col_type(col)

        join_equalities = _build_join_equalities(spec, row_bindings, self.instance)
        for left_var, right_var in join_equalities:
            variables[left_var] = _dtype_for_solver_var(left_var, self.instance)
            variables[right_var] = _dtype_for_solver_var(right_var, self.instance)

        target_relations = tuple(dict.fromkeys(
            binding.relation for binding in row_bindings.values()
        ))
        return SolverConstraint(
            target_relations=target_relations,
            constraints=constraints,
            join_equalities=join_equalities,
            variables=variables,
        ), row_bindings
```

- [ ] **Step 3: Write constraint rewriting and extraction helpers**

```python
def _rewrite_constraint_for_binding(
    constraint: exp.Expression,
    binding: RowBinding,
    instance: Instance,
) -> exp.Expression | None:
    rewritten = constraint.copy()
    has_columns = False
    for col in list(rewritten.find_all(exp.Column)):
        col_id = column_identity(col)
        if col_id is not None and col_id.relation is not None:
            physical = col_id.relation.name.normalized
            col_name = col_id.name.normalized
        else:
            physical = normalize_name(col.table or "")
            col_name = col.name
        if physical != binding.table:
            continue
        has_columns = True
        new_col = _solver_column(instance, physical, col_name, row_scope=f"r{binding.row}")
        orig_type = getattr(col, "type", None)
        if orig_type is not None and getattr(new_col, "type", None) is None:
            new_col.type = orig_type
        col.replace(new_col)
    return rewritten if has_columns else None


def _build_join_equalities(
    spec: BranchSpec,
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> List[Tuple[SolverVar, SolverVar]]:
    equalities: List[Tuple[SolverVar, SolverVar]] = []
    seen: Set[Tuple[SolverVar, SolverVar]] = set()
    for _rep, members in spec.equivalences.groups().items():
        if len(members) < 2:
            continue
        member_bindings: List[Tuple[ColumnId, RowBinding]] = []
        for member in members:
            table_name = member.relation.name.normalized if member.relation and member.relation.name else ""
            binding = _find_binding_for_column(table_name, row_bindings)
            if binding is not None:
                member_bindings.append((member, binding))
        for i in range(len(member_bindings) - 1):
            m1, b1 = member_bindings[i]
            m2, b2 = member_bindings[i + 1]
            v1 = SolverVar(column_id=m1, relation_id=b1.relation, row_scope=f"r{b1.row}")
            v2 = SolverVar(column_id=m2, relation_id=b2.relation, row_scope=f"r{b2.row}")
            pair = (v1, v2)
            if pair not in seen:
                seen.add(pair)
                equalities.append(pair)
    return equalities


def _find_binding_for_column(
    table_name: str, row_bindings: Dict[str, RowBinding],
) -> RowBinding | None:
    for binding in row_bindings.values():
        if binding.table == table_name:
            return binding
    return None


def _collect_solver_vars(expr: exp.Expression, variables: Dict[SolverVar, DataType]) -> None:
    for col in expr.find_all(exp.Column):
        var = solver_var(col)
        dtype = col_type(col)
        if var is not None and dtype is not None:
            variables[var] = dtype


def _dtype_for_solver_var(var: SolverVar, instance: Instance) -> DataType:
    col_name = var.column_id.name.normalized
    col_type_str = _lookup_col_type(instance, var.relation_id, col_name)
    if col_type_str:
        try:
            return DataType.build(col_type_str)
        except Exception:
            pass
    return DataType.build("TEXT")


def _rows_from_solver_result(
    assignments: Dict[SolverVar, Any],
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    cells: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for var, value in assignments.items():
        if not isinstance(var, SolverVar):
            continue
        table = var.relation_id.name.normalized if var.relation_id.name else ""
        col_name = var.column_id.name.normalized
        row_scope = var.row_scope or "r0"
        try:
            row_idx = int(row_scope.lstrip("r"))
        except ValueError:
            row_idx = 0
        if row_idx >= 1000:
            continue
        cells.setdefault((table, row_idx), {})[col_name] = value
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (table, _row_idx), values in sorted(cells.items()):
        rows.setdefault(table, []).append(values)
    return rows


def _fallback_rows(
    spec: BranchSpec, instance: Instance, row_bindings: Dict[str, RowBinding],
) -> Dict[str, List[Dict[str, Any]]]:
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for _key, req in spec.requirements.items():
        physical = req.table
        if physical not in instance.tables:
            continue
        for row_index in range(max(req.min_rows, 1)):
            row: Dict[str, Any] = _extract_fixed_values(req.constraints)
            for col_name in instance.tables[physical]:
                if col_name in row:
                    continue
                try:
                    row[col_name] = instance.builder.generate_value(physical, col_name, row_context=row)
                except Exception:
                    pass
            rows.setdefault(physical, []).append(row)
    return rows
```

- [ ] **Step 4: Write row binding and gold row helpers**

```python
def _build_gold_row_bindings(spec: BranchSpec) -> Dict[str, RowBinding]:
    bindings: Dict[str, RowBinding] = {}
    alias_scoped_tables = {
        req.table for _key, req in spec.requirements.items() if req.alias
    }
    for _key, req in spec.requirements.items():
        physical = req.table
        if physical in alias_scoped_tables and not req.alias:
            continue
        alias = normalize_name(req.alias) if req.alias else physical
        base_rel = req.relation
        if alias != physical:
            rel = relation_id(base_rel.kind, base_rel.name, alias=identifier_name(alias))
        else:
            rel = base_rel
        for row_index in range(max(req.min_rows, 1)):
            binding = RowBinding(relation=rel, row=row_index)
            bindings[_solver_table_key(binding)] = binding
    return bindings


def _bindings_for_requirement(
    table_key, req: TableConstraint, row_bindings: Dict[str, RowBinding],
) -> List[RowBinding]:
    physical = req.table
    alias = normalize_name(req.alias) if req.alias else physical
    return [
        b for b in row_bindings.values()
        if b.table == physical and normalize_name(b.alias or "") == alias
    ]
```

- [ ] **Step 5: Verify Resolver can be imported**

Run: `python -c "from parseval.symbolic.speculate import Resolver"`
Expected: No error

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): write Resolver with global solving"
```

---

### Task 4: Row Completion, Scalar Subqueries, Materialization

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Write the remaining helpers: `_complete_gold_rows`, scalar subquery satisfaction, materialization, and evaluation validation.

- [ ] **Step 1: Write `_complete_gold_rows`**

```python
def _complete_gold_rows(
    rows: Dict[str, List[Dict[str, Any]]],
    row_bindings: Dict[str, RowBinding],
    spec: BranchSpec,
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    builder = type(instance.builder)(instance.schema_spec)
    for table_name in instance.tables:
        for existing_row in instance.get_rows(_relation_for_table(instance, table_name)):
            builder.runtime.remember_row(table_name, {k: v.concrete for k, v in existing_row.items()})

    pending_rows = {t: [dict(r) for r in trs] for t, trs in rows.items()}
    completed: Dict[str, List[Dict[str, Any]]] = {}
    group_values: Dict[Tuple[str, str], Any] = {}
    unique_values: Dict[Tuple[str, str], Set[Any]] = {}

    for table_name, schema in instance.tables.items():
        rel = _relation_for_table(instance, table_name)
        for col_name in schema:
            if not instance.is_unique(rel, physical_column(col_name, rel)):
                continue
            key = (table_name, col_name)
            values = unique_values.setdefault(key, set())
            for existing_row in instance.get_rows(rel):
                if col_name in existing_row.columns:
                    values.add(existing_row[col_name].concrete)

    ordered_bindings = sorted(
        row_bindings.values(),
        key=lambda b: (b.table, normalize_name(b.alias or ""), b.row),
    )
    for binding in ordered_bindings:
        table_rows = pending_rows.setdefault(binding.table, [])
        row = table_rows.pop(0) if table_rows else {}
        req = _requirement_for_binding(spec, binding)
        if req is not None:
            fk_columns = _gold_fk_columns(instance, binding.table)
            for col_name, value in _extract_fixed_values(req.constraints).items():
                row[col_name] = value
            for col_name in req.group_key_columns:
                col_key = col_name.name.normalized if hasattr(col_name, "name") else str(col_name)
                key = (binding.table, col_key)
                if col_key in row:
                    group_values.setdefault(key, row[col_key])
                if key not in group_values:
                    try:
                        group_values[key] = builder.generate_value(binding.table, col_key, row_context=row)
                    except Exception:
                        pass
                if key in group_values:
                    row[col_key] = group_values[key]
            for col_name in instance.tables.get(binding.table, {}):
                if col_name in row:
                    continue
                try:
                    row[col_name] = builder.generate_value(binding.table, col_name, row_context=row)
                except Exception:
                    pass
            # Unique column handling
            for col_name in instance.tables.get(binding.table, {}):
                binding_rel = _relation_for_table(instance, binding.table)
                if not instance.is_unique(binding_rel, physical_column(col_name, binding_rel)):
                    continue
                key = (binding.table, col_name)
                seen_values = unique_values.setdefault(key, set())
                value = row.get(col_name)
                if value is None or value in seen_values:
                    if col_name in fk_columns:
                        row.pop(col_name, None)
                        continue
                    context = dict(row)
                    context.pop(col_name, None)
                    generated = False
                    for _ in range(16):
                        try:
                            value = builder.generate_value(binding.table, col_name, row_context=context)
                            if value not in seen_values:
                                row[col_name] = value
                                generated = True
                                break
                        except Exception:
                            break
                    if not generated:
                        row.pop(col_name, None)
                        continue
                if col_name in row:
                    seen_values.add(row[col_name])
        builder.runtime.remember_row(binding.table, row)
        completed.setdefault(binding.table, []).append(row)

    # High LIMIT support: clone rows to satisfy min_rows
    MAX_TOTAL_ROWS = 500
    for table_key, req in spec.requirements.items():
        physical = normalize_name(req.table.split("__", 1)[0] if "__" in req.table else req.table)
        if physical not in completed or not completed[physical]:
            continue
        target = min(req.min_rows, MAX_TOTAL_ROWS)
        current_rows = completed[physical]
        while len(current_rows) < target:
            base_row = current_rows[-1]
            new_row = dict(base_row)
            phys_rel = _relation_for_table(instance, physical)
            for col_name in instance.tables.get(physical, {}):
                if instance.is_unique(phys_rel, physical_column(col_name, phys_rel)):
                    context = dict(new_row)
                    context.pop(col_name, None)
                    try:
                        new_row[col_name] = builder.generate_value(physical, col_name, row_context=context)
                    except Exception:
                        pass
            builder.runtime.remember_row(physical, new_row)
            current_rows.append(new_row)

    for table, table_rows in pending_rows.items():
        completed.setdefault(table, []).extend(table_rows)
    return completed


def _gold_fk_columns(instance: Instance, table: str) -> Set[str]:
    columns: Set[str] = set()
    rel = _relation_for_table(instance, table)
    for fk in instance.get_foreign_key(rel):
        for identifier in fk.expressions:
            matched = _match_column(instance, rel, identifier.name)
            if matched:
                columns.add(matched)
    return columns


def _requirement_for_binding(
    spec: BranchSpec, binding: RowBinding,
) -> Optional[TableConstraint]:
    alias = normalize_name(binding.alias or binding.table)
    for _key, req in spec.requirements.items():
        if req.table != binding.table:
            continue
        req_alias = normalize_name(req.alias) if req.alias else req.table
        if req_alias == alias:
            return req
    return None
```

- [ ] **Step 2: Write scalar subquery satisfaction**

```python
def _satisfy_gold_scalar_subqueries(
    spec: BranchSpec, plan: Plan,
    rows: Dict[str, List[Dict[str, Any]]],
    instance: Instance, dialect: str,
) -> None:
    seen: Set[str] = set()
    for atom in spec.deferred:
        if not isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            continue
        atom_key = atom.sql(dialect=dialect)
        if atom_key in seen:
            continue
        seen.add(atom_key)
        left, right = atom.this, atom.expression
        if right and right.find(exp.Subquery):
            outer_expr, subquery_expr = left, right
        elif left and left.find(exp.Subquery):
            outer_expr, subquery_expr = right, left
        else:
            continue
        # Try to solve the scalar subquery comparison
        subquery = subquery_expr if isinstance(subquery_expr, exp.Subquery) else subquery_expr.find(exp.Subquery)
        if subquery is None:
            continue
        subplan = _find_subplan_for_subquery(plan, subquery, dialect)
        inner_expr = _scalar_subquery_operand_expression(subplan)
        if inner_expr is None:
            continue
        # Use the solver to find values satisfying the comparison
        _solve_scalar_comparison(atom, outer_expr, inner_expr, rows, instance, dialect)
```

- [ ] **Step 3: Write materialization helpers**

```python
def _materialize_rows(instance: Instance, rows: Dict[str, List[Dict[str, Any]]]) -> None:
    for table_name in _gold_materialization_order(instance, rows):
        for row in rows.get(table_name, []):
            instance.create_row(_relation_for_table(instance, table_name), values=row)


def _gold_materialization_order(instance: Instance, rows: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    requested = [t for t in rows if t in instance.tables]
    requested_set = set(requested)
    ordered: List[str] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(table_name: str) -> None:
        if table_name in visited or table_name in visiting:
            return
        visiting.add(table_name)
        for fk in instance.get_foreign_key(_relation_for_table(instance, table_name)):
            ref = fk.args.get("reference")
            if ref is None:
                continue
            ref_table_node = ref.find(exp.Table)
            if ref_table_node and normalize_name(ref_table_node.name) in requested_set:
                visit(normalize_name(ref_table_node.name))
        visiting.remove(table_name)
        visited.add(table_name)
        ordered.append(table_name)

    for t in requested:
        visit(t)
    return ordered
```

- [ ] **Step 4: Write evaluation validation helpers**

```python
def _gold_candidate_has_output(
    plan: Plan, instance: Instance,
    rows_per_table: Dict[str, List[Dict[str, Any]]],
    dialect: str = "sqlite",
) -> bool:
    checkpoint = instance.checkpoint() if rows_per_table else None
    try:
        if rows_per_table:
            _materialize_rows(instance, rows_per_table)
        tree = BranchTree()
        ctx = PlanEvaluator(plan, instance, dialect).evaluate_context(tree)
        if any(table.rows for table in ctx.tables.values()):
            return True
        return _gold_has_positive_evaluator_observations(tree)
    except Exception:
        return False
    finally:
        if checkpoint is not None:
            instance.rollback(checkpoint)


def _gold_has_positive_evaluator_observations(tree: BranchTree) -> bool:
    has_filter_nodes = False
    for node in tree.nodes:
        if node.site in {"filter", "join_on", "having", "case_arm"}:
            has_filter_nodes = True
            all_true = all(
                BranchType.ATOM_TRUE in node.observed_outcomes(aid)
                for aid, _ in enumerate(node.atoms)
            )
            if all_true:
                return True
        elif node.site == "group":
            has_filter_nodes = True
            if node.observed_outcomes(0):
                return True
    return not has_filter_nodes
```

- [ ] **Step 5: Write remaining subplan helpers**

Write `_find_subplan_for_subquery`, `_scalar_subquery_operand_expression`, `_solve_scalar_comparison`, and the remaining Propagator subplan correlation methods (`_propagate_subplan`, `_propagate_in_subplan`, `_propagate_scalar_subplan`, etc.).

These follow the same pattern as existing code but use `column_identity(col)` for identity resolution.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): write completion, scalar subquery, and materialization helpers"
```

---

### Task 5: Top-Level `speculate()` and `__all__`

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Write `speculate()` function**

```python
def speculate(
    plan: Plan,
    instance: Instance,
    dialect: str = "sqlite",
    config: Optional[SpeculateConfig] = None,
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    if config is None:
        config = SpeculateConfig.gold_non_empty()

    propagator = Propagator(plan, instance, dialect, config=config)
    solver = Solver(dialect=dialect)
    resolver = Resolver(plan, instance, dialect, solver=solver)

    branch_specs = propagator.propagate()
    logger.info("Generated %d branch specs", len(branch_specs))

    results = []
    for spec in branch_specs:
        if not spec.requirements:
            continue
        try:
            rows = resolver.resolve(spec)
        except Exception as exc:
            logger.debug("spec %s failed: %s", spec.branch, exc)
            rows = {}
        if rows:
            results.append((spec.branch, rows))
    return results
```

- [ ] **Step 2: Write `__all__`**

```python
__all__ = [
    "BranchSpec",
    "Propagator",
    "Resolver",
    "SpeculateConfig",
    "TableConstraint",
    "speculate",
]
```

- [ ] **Step 3: Verify the module loads cleanly**

Run: `python -c "from parseval.symbolic.speculate import speculate, Propagator, Resolver, BranchSpec, SpeculateConfig, TableConstraint"`
Expected: No error

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): write speculate() top-level API"
```

---

### Task 6: Integration Verification

**Files:**
- Test: `tests/solver/`
- Test: `tests/plan/`
- Test: `tests/symbolic/`

- [ ] **Step 1: Run solver identity tests**

Run: `pytest tests/solver/test_solver_identity.py -v`
Expected: All 3 tests PASS

- [ ] **Step 2: Run full solver test suite**

Run: `pytest tests/solver/ -v`
Expected: All PASS

- [ ] **Step 3: Run plan tests**

Run: `pytest tests/plan/ -v`
Expected: All PASS

- [ ] **Step 4: Run symbolic tests**

Run: `pytest tests/symbolic/ -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All PASS (or only pre-existing failures)

- [ ] **Step 6: Final commit if fixes needed**

```bash
git add -A
git commit -m "fix(speculate): integration test fixes"
```
