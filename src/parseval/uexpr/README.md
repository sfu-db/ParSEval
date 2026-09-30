# Concrete evaluation

`UExprEvaluator(arena, instance).evaluate_query(root)` returns a `BagValue` or
`SequenceValue` directly. The evaluator, runtime values, and function callbacks
live in `evaluate.py`; expression nodes,
sorts, and function signatures remain in `parseval.terms`.

`BagValue.entries` contains `BagEntry(row, multiplicity)` values. Use
`expanded_rows()` when individual duplicate occurrences are needed.
`SequenceValue.rows` preserves result order. Coverage goals are closed bag
terms whose positive support can be checked through `evaluate_query`.

## Function callbacks

Override the evaluator's class dictionaries to customize callbacks:

```python
from parseval.uexpr import UExprEvaluator, strict

class CustomEvaluator(UExprEvaluator):
    SCALAR_FUNCTIONS = {
        **UExprEvaluator.SCALAR_FUNCTIONS,
        "my_double": strict(lambda args: args[0] * 2),
    }
    AGGREGATE_FUNCTIONS = {
        **UExprEvaluator.AGGREGATE_FUNCTIONS,
        "my_count": lambda spec, values: len(values),
    }
    LAZY_SCALAR_FUNCTIONS = {
        **UExprEvaluator.LAZY_SCALAR_FUNCTIONS,
        "first_argument": lambda args: args[0](),
    }

evaluator = CustomEvaluator(arena, instance)
result = evaluator.evaluate_query(root)
```

- Scalar callbacks receive a tuple of evaluated values, including `None` for
  SQL NULL. `strict(callback)` propagates NULL without calling the callback.
- Lazy scalar callbacks receive zero-argument callables. Each argument is
  evaluated at most once within that invocation, only when requested. Builtin
  `COALESCE` uses this mechanism.
- Aggregate callbacks receive the existing `AggregateSpec` and a tuple of input
  values after `FILTER` and `DISTINCT`. Duplicates, NULLs, and empty inputs are
  preserved for the callback to interpret. Star aggregates receive one `1` per
  admitted row.
- Keys are semantic operator strings, or `FunctionId` / `AggregateSpecId` values
  from the same catalog context. ID registrations take precedence over operator
  registrations. Aggregates fall back to their semantic kind; casts and extracts
  fall back to their operator family. Eager scalar callbacks take precedence over
  lazy callbacks for the same key. To replace an eager callback with a lazy one,
  omit that key from the subclass's `SCALAR_FUNCTIONS` dictionary.
- Dictionary unpacking preserves inherited callbacks without modifying the base
  class or sibling subclasses. Callback exceptions propagate to the caller.

Register custom SQL names and signatures with the catalog before lowering a
query. Then use the declaration's `.function` or `.aggregate` identity as the
callback key, including when its specification has no semantic operator string.
Registering a callback alone does not teach the parser a new SQL name.

`measure_coverage`, `target_is_covered`, and `validate_instance` also accept
`evaluator_class=CustomEvaluator` so concrete checks can use the same interpretation.

Callbacks extend concrete function execution only. They do not add symbolic SMT
encodings, change compiler rewrite rules, or supply handlers for unsupported
expression nodes. The evaluator uses integer multiplicities and finite support
plans; it is not a generic U-semiring interpreter. Window and relation-binding
evaluation remain unsupported. Builtin callbacks retain the current Python-based
scalar semantics and do not claim full database-dialect conformance.
