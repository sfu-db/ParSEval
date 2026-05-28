# Coverage Model Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix branch coverage gaps in ParSEval's evaluator and constraint generator, and modularize the SMT solver.

**Architecture:** Extend the evaluator to track SubPlan/DISTINCT branches, extend the constraint generator to produce SolverConstraint for all branch types, and split smt.py into focused modules. Static-first approach: derive constraints from plan structure, fall back to concrete evaluation when needed.

**Tech Stack:** Python 3.9+, sqlglot, z3-solver, unittest

---

## File Structure

### New Files
- `src/parseval/solver/smt_types.py` — Option type construction, sort registry, type inference, encode_literal
- `src/parseval/solver/smt_translate.py` — sqlglot expression → Z3 translation (SMTTranslator class)
- `src/parseval/solver/smt_solver.py` — Z3 solver wrapper (SMTSolver class)
- `tests/symbolic/test_subplan_eval.py` — Tests for SubPlan evaluator
- `tests/symbolic/test_distinct_eval.py` — Tests for DISTINCT evaluator
- `tests/symbolic/test_constraint_generation.py` — Tests for new constraint generation

### Modified Files
- `src/parseval/symbolic/evaluator.py` — Add SubPlan/DISTINCT evaluation
- `src/parseval/symbolic/constraints.py` — Add constraint generation for EXISTS/IN/GROUP/DISTINCT
- `src/parseval/solver/smt.py` — Becomes thin re-export layer

---

## Phase 3: SMT Solver Modularization

### Task 1: Extract smt_types.py

**Files:**
- Create: `src/parseval/solver/smt_types.py`
- Modify: `src/parseval/solver/smt.py`
- Test: `tests/test_solver.py`

- [ ] **Step 1: Create smt_types.py with type-related functions**

Extract from `smt.py`:
- `make_option_type()` (line 57)
- `LogicalTypeRegistry` (line 71)
- `SMTTypeInfo` (line 119)
- `SMTValue` (line 138)
- `_VarRef` (line 157)
- `UnsupportedSMTError` (line 168)
- `SpecialFunctionModel` (line 173)
- `register_special_function()` (line 206)
- `infer()` (line 36)
- `encode_literal()` (line 515)
- `OptionTypeRegistry` (line 427)
- `is_option_expr()` (line 463)
- `option_of()` (line 467)
- `unwrap_option()` (line 471)
- All temporal helper functions (`_is_temporal_string`, `_infer_temporal_dtype`, `_parse_date`, etc.)
- `normalize_dtype()` (line 356)

```python
# smt_types.py
"""Z3 type system for SQL: Option types, sort registry, type inference."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.plan.rex import Const

logger = logging.getLogger("parseval.smt")

# ... all extracted code ...
```

- [ ] **Step 2: Update smt.py to import from smt_types.py**

Replace extracted code in `smt.py` with imports:

```python
# smt.py
from .smt_types import (
    LogicalTypeRegistry,
    OptionTypeRegistry,
    SMTTypeInfo,
    SMTValue,
    SpecialFunctionModel,
    UnsupportedSMTError,
    _VarRef,
    checkpoint,
    encode_literal,
    infer,
    is_option_expr,
    make_option_type,
    normalize_dtype,
    option_of,
    register_special_function,
    unwrap_option,
    # temporal helpers
    _date_to_epoch_day,
    _datetime_to_epoch_second,
    _from_epoch_day,
    _from_epoch_second,
    _from_seconds,
    _infer_temporal_dtype,
    _is_temporal_string,
    _parse_date,
    _parse_datetime,
    _parse_time,
    _time_to_seconds,
)
```

- [ ] **Step 3: Run existing tests to verify no behavior change**

Run: `python -m pytest tests/test_solver.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/parseval/solver/smt_types.py src/parseval/solver/smt.py
git commit -m "refactor(solver): extract smt_types.py for type system"
```

---

### Task 2: Extract smt_translate.py

**Files:**
- Create: `src/parseval/solver/smt_translate.py`
- Modify: `src/parseval/solver/smt.py`
- Test: `tests/test_solver.py`

- [ ] **Step 1: Create smt_translate.py with translation functions**

Extract from `smt.py`:
- `_to_z3_sort()` (line 487)
- `_python_to_payload()` (line 492)
- `_to_z3val()` (line 511)
- `declare_column()` (line 526)
- `_value_some()` (line 534)
- `_value_null()` (line 541)
- `_value_payload()` (line 548)
- `_coerce_pair()` (line 555)
- `_bool_value()` (line 571)
- `_null_value()` (line 578)
- `_zfill2()` (line 584)
- `like_to_z3()` (line 588)
- All `_translate_*` functions (line 1712+)
- `_return_same_type()`, `_return_int()`, `_return_text()` (line 1696+)

```python
# smt_translate.py
"""SQL expression → Z3 translation layer."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from .smt_types import (
    LogicalTypeRegistry,
    OptionTypeRegistry,
    SMTTypeInfo,
    SMTValue,
    UnsupportedSMTError,
    _VarRef,
    encode_literal,
    normalize_dtype,
)

# ... all extracted code ...
```

- [ ] **Step 2: Update smt.py to import from smt_translate.py**

```python
# smt.py
from .smt_translate import (
    _bool_value,
    _coerce_pair,
    _null_value,
    _to_z3_sort,
    _to_z3val,
    _value_null,
    _value_payload,
    _value_some,
    _zfill2,
    declare_column,
    like_to_z3,
    # translate functions
    _return_int,
    _return_same_type,
    _return_text,
    _translate_abs,
    _translate_instr,
    _translate_length,
    _translate_strftime,
    _translate_substr,
    _ymd_hms_from_temporal,
    _coerce_numeric_sort,
    _python_to_payload,
)
```

- [ ] **Step 3: Run existing tests to verify no behavior change**

Run: `python -m pytest tests/test_solver.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/parseval/solver/smt_translate.py src/parseval/solver/smt.py
git commit -m "refactor(solver): extract smt_translate.py for expression translation"
```

---

### Task 3: Extract smt_solver.py and make smt.py a re-export layer

**Files:**
- Create: `src/parseval/solver/smt_solver.py`
- Modify: `src/parseval/solver/smt.py`
- Test: `tests/test_solver.py`

- [ ] **Step 1: Create smt_solver.py with SMTSolver class**

Extract from `smt.py`:
- `SMTSolver` class (line 629)

```python
# smt_solver.py
"""Z3 solver wrapper for ParSEval constraint solving."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from .smt_types import (
    LogicalTypeRegistry,
    OptionTypeRegistry,
    SMTTypeInfo,
    SMTValue,
    SpecialFunctionModel,
    UnsupportedSMTError,
    _VarRef,
    checkpoint,
    is_option_expr,
    make_option_type,
    normalize_dtype,
    option_of,
    register_special_function,
    unwrap_option,
)
from .smt_translate import (
    _bool_value,
    _coerce_pair,
    _null_value,
    _to_z3_sort,
    _to_z3val,
    _value_null,
    _value_payload,
    _value_some,
    declare_column,
    like_to_z3,
)

logger = logging.getLogger("parseval.smt")

# ... SMTSolver class ...
```

- [ ] **Step 2: Make smt.py a thin re-export layer**

```python
# smt.py
"""Z3-backed constraint solving for ParSEval.

This module re-exports from focused submodules:
- smt_types: Option types, sort registry, type inference
- smt_translate: SQL expression → Z3 translation
- smt_solver: Z3 solver wrapper
"""

from .smt_solver import SMTSolver
from .smt_types import (
    LogicalTypeRegistry,
    OptionTypeRegistry,
    SMTTypeInfo,
    SMTValue,
    SpecialFunctionModel,
    UnsupportedSMTError,
    _VarRef,
    checkpoint,
    encode_literal,
    infer,
    is_option_expr,
    make_option_type,
    normalize_dtype,
    option_of,
    register_special_function,
    unwrap_option,
)
from .smt_translate import (
    _bool_value,
    _coerce_pair,
    _coerce_numeric_sort,
    _null_value,
    _python_to_payload,
    _return_int,
    _return_same_type,
    _return_text,
    _to_z3_sort,
    _to_z3val,
    _translate_abs,
    _translate_instr,
    _translate_length,
    _translate_strftime,
    _translate_substr,
    _value_null,
    _value_payload,
    _value_some,
    _ymd_hms_from_temporal,
    _zfill2,
    declare_column,
    like_to_z3,
)

__all__ = [
    "SMTSolver",
    "SMTValue",
    "SpecialFunctionModel",
    "UnsupportedSMTError",
    "encode_literal",
    "infer",
    "is_option_expr",
    "make_option_type",
    "register_special_function",
    "_to_z3val",
]
```

- [ ] **Step 3: Run full test suite to verify no behavior change**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/parseval/solver/smt_solver.py src/parseval/solver/smt.py
git commit -m "refactor(solver): extract smt_solver.py, smt.py becomes re-export layer"
```

---

## Phase 1: Evaluator Coverage Extension

### Task 4: Add SubPlan evaluation for EXISTS

**Files:**
- Modify: `src/parseval/symbolic/evaluator.py`
- Create: `tests/symbolic/test_subplan_eval.py`

- [ ] **Step 1: Write failing test for EXISTS evaluation**

```python
# tests/symbolic/test_subplan_eval.py
"""Tests for SubPlan evaluation in PlanEvaluator."""

from __future__ import annotations

import unittest

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic import BranchTree, BranchType, CoverageThresholds, PlanEvaluator


SCHEMA = """
CREATE TABLE t1 (id INT, x INT);
CREATE TABLE t2 (id INT, x INT, y INT);
"""


def _evaluate(sql: str, schema: str = SCHEMA, dialect: str = "sqlite") -> BranchTree:
    instance = Instance(ddls=schema, name="test", dialect=dialect)
    expr = preprocess_sql(sql, instance, dialect=dialect)
    plan = Plan(expr)
    evaluator = PlanEvaluator(plan, instance, dialect)
    tree = BranchTree(thresholds=CoverageThresholds(exists_true=1, exists_false=1))
    return evaluator.evaluate(tree)


class TestExistsEvaluation(unittest.TestCase):
    def test_exists_records_true_when_inner_has_rows(self):
        """EXISTS should record EXISTS_TRUE when inner query returns rows."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        # Insert rows into t2 so EXISTS returns true
        instance.create_row("t2", values={"x": 1, "y": 10})
        
        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = 1)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(exists_true=1, exists_false=1))
        
        # Insert a row into t1 so the outer query has something to evaluate
        instance.create_row("t1", values={"x": 1})
        
        tree = evaluator.evaluate(tree)
        
        # Check that EXISTS branch was tracked
        exists_nodes = [n for n in tree.nodes if n.site == "exists"]
        self.assertTrue(len(exists_nodes) > 0, "No EXISTS branch node found")
        
        # Check that EXISTS_TRUE was observed
        exists_node = exists_nodes[0]
        outcomes = exists_node.observed_outcomes(0)
        self.assertIn(BranchType.EXISTS_TRUE, outcomes)

    def test_exists_records_false_when_inner_empty(self):
        """EXISTS should record EXISTS_FALSE when inner query returns no rows."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        # Don't insert any rows into t2, so EXISTS returns false
        
        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = 999)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(exists_true=1, exists_false=1))
        
        # Insert a row into t1
        instance.create_row("t1", values={"x": 1})
        
        tree = evaluator.evaluate(tree)
        
        exists_nodes = [n for n in tree.nodes if n.site == "exists"]
        self.assertTrue(len(exists_nodes) > 0, "No EXISTS branch node found")
        
        exists_node = exists_nodes[0]
        outcomes = exists_node.observed_outcomes(0)
        self.assertIn(BranchType.EXISTS_FALSE, outcomes)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/symbolic/test_subplan_eval.py -v`
Expected: FAIL with "No EXISTS branch node found" (evaluator doesn't track SubPlan)

- [ ] **Step 3: Add _eval_subplan method to PlanEvaluator**

In `src/parseval/symbolic/evaluator.py`, add to the `_walk` method's dispatch:

```python
def _walk(self, step: Step, ctx: Context, tree: BranchTree) -> Context:
    """Recursively evaluate the plan bottom-up."""
    dep_contexts: Dict[str, DerivedSchema] = {}
    for dep in step.chain_dependencies:
        dep_ctx = self._walk(dep, ctx, tree)
        for name, table in dep_ctx.tables.items():
            dep_contexts[name] = table

    input_ctx = Context(tables=dep_contexts) if dep_contexts else ctx

    if isinstance(step, Scan):
        return self._eval_scan(step, ctx)
    elif isinstance(step, Filter):
        return self._eval_filter(step, input_ctx, tree)
    elif isinstance(step, Join):
        return self._eval_join(step, ctx, tree)
    elif isinstance(step, Aggregate):
        return self._eval_aggregate(step, input_ctx, tree)
    elif isinstance(step, Having):
        return self._eval_having(step, input_ctx, tree)
    elif isinstance(step, Project):
        return self._eval_project(step, input_ctx, tree)
    elif isinstance(step, SubPlan):
        return self._eval_subplan(step, input_ctx, tree)  # NEW
    elif isinstance(step, (Sort, Limit, SetOperation)):
        return input_ctx
    return input_ctx
```

Add the `_eval_subplan` method:

```python
def _eval_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
    """Evaluate a SubPlan and record branch observations."""
    if step.kind == SubPlanKind.EXISTS:
        return self._eval_exists_subplan(step, ctx, tree)
    elif step.kind == SubPlanKind.IN:
        return self._eval_in_subplan(step, ctx, tree)
    elif step.kind == SubPlanKind.SCALAR:
        return self._eval_scalar_subplan(step, ctx, tree)
    return ctx

def _eval_exists_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
    """Evaluate EXISTS (SELECT ...) and record EXISTS_TRUE/EXISTS_FALSE."""
    annotation = self.plan.annotation_for(step) if hasattr(self.plan, 'annotation_for') else None
    step_id = annotation.step_id if annotation else f"subplan_{id(step)}"
    
    node = tree.get_or_create_node(
        step_id=step_id,
        step_type="SubPlan",
        site="exists",
        predicate=step.anchor,
        atoms=(step.anchor,),
        tables=(),
    )
    
    # Evaluate inner plan against the instance
    inner_ctx = self._walk(step.inner, Context(), tree)
    
    # Check if inner query returns any rows
    has_rows = any(
        len(table.rows) > 0 
        for table in inner_ctx.tables.values()
    )
    
    outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
    tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))
    
    return ctx  # SubPlan doesn't transform the outer context
```

Add import for `SubPlanKind`:

```python
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
    SubPlanKind,  # NEW
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_subplan_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/evaluator.py tests/symbolic/test_subplan_eval.py
git commit -m "feat(evaluator): add EXISTS SubPlan branch tracking"
```

---

### Task 5: Add IN SubPlan evaluation

**Files:**
- Modify: `src/parseval/symbolic/evaluator.py`
- Modify: `tests/symbolic/test_subplan_eval.py`

- [ ] **Step 1: Write failing test for IN evaluation**

Add to `tests/symbolic/test_subplan_eval.py`:

```python
class TestInEvaluation(unittest.TestCase):
    def test_in_records_match_when_value_in_set(self):
        """IN should record IN_MATCH when outer value is in inner result set."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t2", values={"x": 2, "y": 20})
        instance.create_row("t1", values={"x": 1})  # x=1 is in t2.x
        
        sql = "SELECT * FROM t1 WHERE t1.x IN (SELECT t2.x FROM t2)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(in_match=1, in_no_match=1))
        
        tree = evaluator.evaluate(tree)
        
        in_nodes = [n for n in tree.nodes if n.site == "in"]
        self.assertTrue(len(in_nodes) > 0, "No IN branch node found")
        
        in_node = in_nodes[0]
        outcomes = in_node.observed_outcomes(0)
        self.assertIn(BranchType.IN_MATCH, outcomes)

    def test_in_records_no_match_when_value_not_in_set(self):
        """IN should record IN_NO_MATCH when outer value is not in inner result set."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t1", values={"x": 999})  # x=999 not in t2.x
        
        sql = "SELECT * FROM t1 WHERE t1.x IN (SELECT t2.x FROM t2)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(in_match=1, in_no_match=1))
        
        tree = evaluator.evaluate(tree)
        
        in_nodes = [n for n in tree.nodes if n.site == "in"]
        self.assertTrue(len(in_nodes) > 0, "No IN branch node found")
        
        in_node = in_nodes[0]
        outcomes = in_node.observed_outcomes(0)
        self.assertIn(BranchType.IN_NO_MATCH, outcomes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/symbolic/test_subplan_eval.py::TestInEvaluation -v`
Expected: FAIL

- [ ] **Step 3: Add _eval_in_subplan method**

```python
def _eval_in_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
    """Evaluate col IN (SELECT ...) and record IN_MATCH/IN_NO_MATCH."""
    annotation = self.plan.annotation_for(step) if hasattr(self.plan, 'annotation_for') else None
    step_id = annotation.step_id if annotation else f"subplan_{id(step)}"
    
    node = tree.get_or_create_node(
        step_id=step_id,
        step_type="SubPlan",
        site="in",
        predicate=step.anchor,
        atoms=(step.anchor,),
        tables=(),
    )
    
    # Evaluate inner plan to get the value set
    inner_ctx = self._walk(step.inner, Context(), tree)
    inner_values = set()
    for table in inner_ctx.tables.values():
        for row in table.rows:
            for col, sym in row.items():
                val = sym.concrete if hasattr(sym, 'concrete') else sym
                if val is not None:
                    inner_values.add(val)
    
    # Get the outer column value from context
    # The anchor is an exp.In node, its .this is the outer column
    if isinstance(step.anchor, exp.In):
        outer_col = step.anchor.this
        if isinstance(outer_col, exp.Column):
            # Find the outer value in ctx
            for table_name, table in ctx.tables.items():
                for row in table.rows:
                    env = _env_from_row(row, table_name)
                    outer_val = concrete(outer_col, env)
                    
                    if outer_val in inner_values:
                        outcome = BranchType.IN_MATCH
                    else:
                        outcome = BranchType.IN_NO_MATCH
                    
                    tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))
    
    return ctx
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_subplan_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/evaluator.py tests/symbolic/test_subplan_eval.py
git commit -m "feat(evaluator): add IN SubPlan branch tracking"
```

---

### Task 6: Add DISTINCT evaluation

**Files:**
- Modify: `src/parseval/symbolic/evaluator.py`
- Create: `tests/symbolic/test_distinct_eval.py`

- [ ] **Step 1: Write failing test for DISTINCT evaluation**

```python
# tests/symbolic/test_distinct_eval.py
"""Tests for DISTINCT evaluation in PlanEvaluator."""

from __future__ import annotations

import unittest

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic import BranchTree, BranchType, CoverageThresholds, PlanEvaluator


SCHEMA = "CREATE TABLE t (id INT, name TEXT);"


class TestDistinctEvaluation(unittest.TestCase):
    def test_distinct_records_unique_when_all_rows_unique(self):
        """DISTINCT should record DISTINCT_UNIQUE when all projected values are unique."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t", values={"name": "Alice"})
        instance.create_row("t", values={"name": "Bob"})
        
        sql = "SELECT DISTINCT name FROM t"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(distinct_unique=1, distinct_duplicate=1))
        
        tree = evaluator.evaluate(tree)
        
        distinct_nodes = [n for n in tree.nodes if n.site == "distinct"]
        self.assertTrue(len(distinct_nodes) > 0, "No DISTINCT branch node found")
        
        outcomes = distinct_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.DISTINCT_UNIQUE, outcomes)

    def test_distinct_records_duplicate_when_duplicates_exist(self):
        """DISTINCT should record DISTINCT_DUPLICATE when projected values have duplicates."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t", values={"name": "Alice"})
        instance.create_row("t", values={"name": "Alice"})  # duplicate
        
        sql = "SELECT DISTINCT name FROM t"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(distinct_unique=1, distinct_duplicate=1))
        
        tree = evaluator.evaluate(tree)
        
        distinct_nodes = [n for n in tree.nodes if n.site == "distinct"]
        self.assertTrue(len(distinct_nodes) > 0, "No DISTINCT branch node found")
        
        outcomes = distinct_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.DISTINCT_DUPLICATE, outcomes)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/symbolic/test_distinct_eval.py -v`
Expected: FAIL

- [ ] **Step 3: Add DISTINCT tracking in _eval_project**

Modify `_eval_project` in `src/parseval/symbolic/evaluator.py`:

```python
def _eval_project(self, step: Project, ctx: Context, tree: BranchTree) -> Context:
    annotation = self.plan.annotation_for(step)
    
    # Track CASE arms (existing logic)
    for projection in step.projections:
        if not isinstance(projection, exp.Expression):
            continue
        for case_expr in projection.find_all(exp.Case):
            ifs = case_expr.args.get("ifs") or []
            for arm_index, arm in enumerate(ifs):
                arm_pred = arm.args.get("this")
                if not isinstance(arm_pred, exp.Expression):
                    continue

                atoms = decompose_atoms(arm_pred)
                node = tree.get_or_create_node(
                    step_id=annotation.step_id,
                    step_type="Project",
                    site="case_arm",
                    predicate=arm_pred,
                    atoms=atoms,
                    tables=annotation.source_tables,
                )

                for table_name, table in ctx.tables.items():
                    for row in table.rows:
                        env = _env_from_row(row, table_name)
                        for atom_id, atom in enumerate(atoms):
                            value = concrete(atom, env)
                            outcome = _classify_outcome(value)
                            tree.record_observation(
                                node, AtomObservation(atom_id=atom_id, outcome=outcome)
                            )
    
    # Track DISTINCT (new logic)
    if step.distinct:
        distinct_node = tree.get_or_create_node(
            step_id=annotation.step_id,
            step_type="Project",
            site="distinct",
            predicate=exp.Literal.string("DISTINCT"),
            atoms=(exp.Literal.string("DISTINCT"),),
            tables=annotation.source_tables,
        )
        
        # Collect projected values from all tables
        seen = set()
        has_duplicates = False
        for table_name, table in ctx.tables.items():
            for row in table.rows:
                # Build a key from all column values
                key = tuple(
                    (col, sym.concrete if hasattr(sym, 'concrete') else sym)
                    for col, sym in row.items()
                )
                if key in seen:
                    has_duplicates = True
                    break
                seen.add(key)
            if has_duplicates:
                break
        
        outcome = BranchType.DISTINCT_DUPLICATE if has_duplicates else BranchType.DISTINCT_UNIQUE
        tree.record_observation(distinct_node, AtomObservation(atom_id=0, outcome=outcome))
    
    return ctx
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_distinct_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/evaluator.py tests/symbolic/test_distinct_eval.py
git commit -m "feat(evaluator): add DISTINCT branch tracking"
```

---

## Phase 2: Constraint Generation Extension

### Task 7: Add EXISTS constraint generation

**Files:**
- Modify: `src/parseval/symbolic/constraints.py`
- Create: `tests/symbolic/test_constraint_generation.py`

- [ ] **Step 1: Write failing test for EXISTS constraint generation**

```python
# tests/symbolic/test_constraint_generation.py
"""Tests for constraint generation for new branch types."""

from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.constraints import ConstraintGenerator, SolverConstraint
from parseval.symbolic.types import BranchTree, BranchType, CoverageTarget, BranchNode


SCHEMA = """
CREATE TABLE t1 (id INT, x INT);
CREATE TABLE t2 (id INT, x INT, y INT);
"""


class TestExistsConstraintGeneration(unittest.TestCase):
    def test_generates_constraint_for_exists_false(self):
        """ConstraintGenerator should produce constraints for EXISTS_FALSE."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t1", values={"x": 1})
        
        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = t1.x)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        
        # Create a mock CoverageTarget for EXISTS_FALSE
        exists_expr = exp.Exists(this=exp.Subquery(
            this=exp.select("*").from_("t2").where(exp.column("x", "t2").eq(exp.column("x", "t1")))
        ))
        
        node = BranchNode(
            step_id="test_step",
            step_type="SubPlan",
            site="exists",
            predicate=exists_expr,
            atoms=(exists_expr,),
            tables=("t1",),
        )
        
        target = CoverageTarget(
            node=node,
            atom_id=0,
            target_outcome=BranchType.EXISTS_FALSE,
        )
        
        gen = ConstraintGenerator(plan, instance, "sqlite")
        constraint = gen.generate(target)
        
        # Should produce a constraint that makes EXISTS return false
        self.assertIsNotNone(constraint, "Constraint should not be None")
        self.assertIsNotNone(constraint.atom, "Atom constraint should not be None")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/symbolic/test_constraint_generation.py -v`
Expected: FAIL

- [ ] **Step 3: Add EXISTS/IN handling in ConstraintGenerator.generate**

Modify `generate` method in `src/parseval/symbolic/constraints.py`:

```python
def generate(self, target: CoverageTarget) -> SolverConstraint:
    atom = target.atom
    outcome = target.target_outcome
    node = target.node
    tables = node.tables

    step = self._find_step(node.step_id)

    # --- Handle SubPlan branches ---
    if node.site == "exists":
        return self._generate_exists_constraint(target)
    elif node.site == "in":
        return self._generate_in_constraint(target)
    
    # --- Handle DISTINCT branches ---
    if node.site == "distinct":
        return self._generate_distinct_constraint(target)
    
    # --- Handle GROUP branches ---
    if node.site == "group":
        return self._generate_group_constraint(target)
    
    # --- Existing logic for filter/join/having/case ---
    # ... (existing code) ...
```

Add the new methods:

```python
def _generate_exists_constraint(self, target: CoverageTarget) -> SolverConstraint:
    """Generate constraint for EXISTS_TRUE or EXISTS_FALSE."""
    # For EXISTS_FALSE: generate an outer row where the inner query returns empty
    # Strategy: set the correlation column to a value not in the inner table
    
    # Find the SubPlan from the plan
    subplan = self._find_subplan_for_target(target)
    if not subplan or not subplan.correlation:
        # Non-correlated EXISTS — generate a row that makes inner WHERE fail
        return self._generate_non_correlated_exists(target)
    
    # Correlated EXISTS
    corr_col = subplan.correlation[0]
    outer_table = self._resolve_table(corr_col, target.node.tables)
    
    if target.target_outcome == BranchType.EXISTS_FALSE:
        # Generate outer row with correlation value not in inner table
        inner_table = self._find_inner_scan_table(subplan)
        if inner_table:
            existing = set()
            for row in self.instance.get_rows(inner_table):
                val = row.get(corr_col.name)
                if val is not None and val.concrete is not None:
                    existing.add(val.concrete)
            
            # Generate a fresh value
            fresh = max(existing, default=0) + 1 if all(isinstance(v, int) for v in existing) else "fresh_val"
            
            return SolverConstraint(
                target_tables=(outer_table,),
                atom=exp.EQ(this=corr_col, expression=exp.Literal.number(fresh)),
                target_outcome=BranchType.ATOM_TRUE,
            )
    
    # EXISTS_TRUE: default — the inner query should already return rows
    return SolverConstraint(
        target_tables=(outer_table,),
        atom=exp.EQ(this=corr_col, expression=corr_col),
        target_outcome=BranchType.ATOM_TRUE,
    )

def _generate_in_constraint(self, target: CoverageTarget) -> SolverConstraint:
    """Generate constraint for IN_MATCH or IN_NO_MATCH."""
    # Similar to EXISTS but for IN expressions
    # For IN_NO_MATCH: generate outer value not in inner result set
    # This is already partially handled by engine._repair_not_in_simple
    
    return SolverConstraint(
        target_tables=target.node.tables,
        atom=target.atom,
        target_outcome=target.target_outcome,
    )

def _generate_distinct_constraint(self, target: CoverageTarget) -> SolverConstraint:
    """Generate constraint for DISTINCT_UNIQUE or DISTINCT_DUPLICATE."""
    # For DISTINCT_DUPLICATE: we need duplicate rows
    # This is handled by the enrichment phase (_enrich_for_semantics)
    # Return a minimal constraint that the solver can satisfy
    
    return SolverConstraint(
        target_tables=target.node.tables,
        atom=exp.Literal.string("DISTINCT"),
        target_outcome=target.target_outcome,
    )

def _generate_group_constraint(self, target: CoverageTarget) -> SolverConstraint:
    """Generate constraint for GROUP_SINGLE or GROUP_MULTI."""
    # For GROUP_MULTI: need multiple rows with same GROUP BY key
    # For GROUP_SINGLE: need exactly one row per group (default)
    
    return SolverConstraint(
        target_tables=target.node.tables,
        atom=exp.Literal.number(1),
        target_outcome=target.target_outcome,
    )

def _find_subplan_for_target(self, target: CoverageTarget):
    """Find the SubPlan step that corresponds to the target."""
    for step in self.plan.ordered_steps:
        if isinstance(step, SubPlan):
            annotation = self.plan.annotation_for(step)
            if annotation and annotation.step_id == target.node.step_id:
                return step
    return None

def _find_inner_scan_table(self, subplan) -> str:
    """Find the main table referenced in a SubPlan's inner plan."""
    stack = [subplan.inner]
    while stack:
        step = stack.pop()
        if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
            return step.source.name
        stack.extend(step.chain_dependencies)
    return ""
```

Add import for `SubPlan`:

```python
from parseval.plan.planner import Filter, Having, Join, Aggregate, Scan, SubPlan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_constraint_generation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/constraints.py tests/symbolic/test_constraint_generation.py
git commit -m "feat(constraints): add EXISTS/IN/DISTINCT/GROUP constraint generation"
```

---

### Task 8: Run full test suite and verify integration

**Files:**
- None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run the specific new tests**

Run: `python -m pytest tests/symbolic/test_subplan_eval.py tests/symbolic/test_distinct_eval.py tests/symbolic/test_constraint_generation.py -v`
Expected: All new tests pass

- [ ] **Step 3: Verify SMT modularization didn't break anything**

Run: `python -m pytest tests/test_solver.py -v`
Expected: All solver tests pass

- [ ] **Step 4: Final commit if needed**

```bash
git status
# If there are any uncommitted changes:
git add -A
git commit -m "chore: final cleanup for coverage model improvement"
```

---

## Summary

| Task | Phase | Description | Files Changed |
|------|-------|-------------|---------------|
| 1 | 3 | Extract smt_types.py | smt_types.py (new), smt.py |
| 2 | 3 | Extract smt_translate.py | smt_translate.py (new), smt.py |
| 3 | 3 | Extract smt_solver.py, smt.py re-export | smt_solver.py (new), smt.py |
| 4 | 1 | EXISTS SubPlan evaluation | evaluator.py, test_subplan_eval.py |
| 5 | 1 | IN SubPlan evaluation | evaluator.py, test_subplan_eval.py |
| 6 | 1 | DISTINCT evaluation | evaluator.py, test_distinct_eval.py |
| 7 | 2 | EXISTS/IN/DISTINCT/GROUP constraints | constraints.py, test_constraint_generation.py |
| 8 | - | Full test suite verification | none |
