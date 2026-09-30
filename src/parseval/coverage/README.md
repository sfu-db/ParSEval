# Concrete evaluation and coverage

`UExprEvaluator(arena, instance).evaluate_query(root)` returns a `BagValue` or
`SequenceValue`. The evaluator, runtime values, witness plans, and coverage
conditions live in this package. Expression nodes, sorts, and function
signatures remain in `parseval.terms`. Compilation and E-SPNF normalization
remain in `parseval.uexpr`.

`BagValue.entries` contains `BagEntry(row, multiplicity)` values. Use
`expanded_rows()` when individual duplicate occurrences are needed.
`SequenceValue.rows` preserves result order.

Override the evaluator's class dictionaries to customize callbacks:

```python
from parseval.coverage.evaluate import UExprEvaluator, strict

class CustomEvaluator(UExprEvaluator):
    SCALAR_FUNCTIONS = {
        **UExprEvaluator.SCALAR_FUNCTIONS,
        "my_double": strict(lambda args: args[0] * 2),
    }
```

`measure_coverage`, `target_is_covered`, and `validate_instance` accept
`evaluator_class=CustomEvaluator`. Callbacks extend concrete function execution
only. They do not add symbolic SMT encodings or compiler rewrite rules.

## Instance-guided coverage

A coverage target is a witnessed obligation: one finite row or unit binding
must reach a semantic site and satisfy typed conditions there. The concrete
evaluator checks the obligation and the SMT interpreter encodes the same
binding and conditions. A target can cover a rejected filter row even when the
query returns no row.

`closed_scopes` walks the compiled expression with lexical `LetRel` bindings.
`support_plans` finds independent finite row domains for E-SPNF products.
`explore_paths` observes complete factor-outcome vectors and changes one
outcome at a time. When no vector is observed, it seeds local outcomes and a
productive path. This lets
generation find feasible rejected-row paths when schema constraints rule out
the productive path.

Conditions currently cover multiplicity intervals, three-valued predicate
truth, scalar NULL, bag cardinality, and group cardinality. Unit witnesses cover
empty/nonempty aggregate inputs and groups of size one, two, and greater than
`group_size` (default three). A group's size is the sum of its input weights
after input joins and filters, before aggregate-specific FILTER/NULL/DISTINCT
processing. The grouped output's weight is still one. Empty input creates a
zero-sized global group, but no GROUP BY groups. `Add` observations distinguish contributing
children, including matched and null-extended outer-join contributions.
For `At(R, a)`, coverage uses `R(a) = 0`, `R(a) = 1`, and `R(a) >= 2`:
`R(a)` is the number of copies of row `a`. A zero outcome requires an
independent finite source of candidate rows. Support scans retain the original
observation scope even when a contributing child introduces nested binders.
`CoverageExplorer` owns concrete observation for one compiled expression.
`CoverageTracker` owns the stable target inventory and records the strongest
evidence for each target. It has no solver dependency, so concrete fixtures,
fuzzing, and symbolic generation share the same reporting model. `generate`
keeps the instance that produced a neighbor as its concrete seed; the solver
tries to retain unobserved seed cells after finding a feasible model, then
shrinks total rows with bounded feasibility checks. Every SAT model is checked
again by the concrete evaluator.

Coverage is deliberately incomplete for aggregate result values and ordered
output positions. Unsupported lexical scopes are reported as unsupported
scopes. The SMT solver starts with small per-relation support, uses symbolic
selectors for existential witnesses, and grows support when more distinct
values are needed. Row counts use natural-number weights. Aggregate-cardinality
targets can defer independent integer unique keys to concrete materialization;
observed or constrained keys remain explicit. Exhausting support returns
`bounded_unsat`, not a proof over unbounded databases. See
[the SMT design](../smt/README.md). The broader coverage architecture and implementation
sequence are in [DESIGN.md](DESIGN.md).

`CoverageReport.complete` describes witness-analysis capability and is false
when unsupported scopes were found. `fully_covered` additionally requires a
concrete witness for every discovered target. Per-target outcomes distinguish
`covered`, `bounded_unsat`, `unknown`, `unsupported`, and targets that were not
attempted because a generation budget was reached.
