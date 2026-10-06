# Concolic scalar execution

This package executes typed scalar operations while retaining their expressions
in the existing Terms arena. It has no dependency on Z3, coverage, or instance
generation.

```python
from parseval.symbolic import Runtime

runtime = Runtime()
x = runtime.input("x", 4)
value = (x + 2) * 3

assert value.concrete == 18
assert value.evaluate({"x": 10}) == 36
assert value.concrete == 18  # Reevaluation does not change the observation.
```

`ZValue` pairs a concrete result with a `ZExpr`. `ZExpr`
inherits `TermView` and references an existing arena and root; it does not define
another expression tree. `ScalarSort` remains the source of SQL type and
nullability information. Inputs use typed `ExternalParameter` Terms. Concrete
assignments belong to a runtime, so interned Terms never carry mutable values.

## Registering scalar functions

Extend the class-level `Runtime.SCALAR_FUNCTIONS` dictionary with callables,
including lambdas. A callback receives a tuple of concrete arguments and returns
one concrete value. Immediate execution and expression reevaluation use the same
registry.

```python
from typing import ClassVar

from parseval.symbolic import Runtime, ScalarFunction, strict
from parseval.terms.sorts import INTEGER, STRING, ScalarSort


class ApplicationRuntime(Runtime):
    SCALAR_FUNCTIONS: ClassVar[dict[str, ScalarFunction]] = {
        **Runtime.SCALAR_FUNCTIONS,
        "twice": strict(lambda args: args[0] * 2),
        "null_label": lambda args: "(null)" if args[0] is None else str(args[0]),
    }


runtime = ApplicationRuntime()
x = runtime.input("x", 4)
value = runtime.call("twice", x, result=ScalarSort(INTEGER))
assert value.concrete == 8
assert value.evaluate({"x": 7}) == 14

nullable = runtime.input("nullable", None, ScalarSort(INTEGER, True))
label = runtime.call("null_label", nullable, result=ScalarSort(STRING))
assert label.concrete == "(null)"
```

Copy the parent dictionary when extending a subclass to keep registrations
isolated. Direct dictionary registration is also supported. Configure callbacks
before creating expressions and keep them fixed while using those expressions;
reevaluation consults the current registry.

Named calls require an explicit result `ScalarSort`. Their argument sorts come
from the input values, and the resulting typed `ScalarCall` retains its argument
expressions. A registered `FunctionId` can also be passed to `call`; its signature
comes from the arena's `Context`, and an ID callback takes precedence over an
operator-name callback. IDs belong to their context and should only be registered
for runtimes using that context.

Callbacks are eager and deterministic. Wrap a callback with `strict` to return
`None` when any argument is NULL. Unwrapped callbacks receive NULL arguments and
define their own handling. Results are checked against the declared type and
nullability on both execution paths. Callback exceptions propagate.

Built-in scalar methods such as `lower()` use this registry too, so a subclass
can supply its own implementation. Native predicate Terms retain their SQL
three-valued semantics. Callback bodies execute on concrete values; they remain
opaque scalar calls in Terms. Registering a concrete callback does not supply an
SMT encoding for that function.

## Organization and scope

| Module | Responsibility |
| --- | --- |
| `base.py` | Concrete/expression pairing, comparisons, NULL tests, casts |
| `numeric.py` | `ZInt` and `ZFloat` operation methods |
| `boolean.py` | Explicit three-valued Boolean operations |
| `string.py` | String operation methods |
| `temporal.py` | Date, time, timestamp, and interval methods |
| `expression.py` | Executable Term views and scalar expression traversal |
| `runtime.py` | Input bindings, Term construction, callback registration and NULL handling |
| `operations.py` | Primitive type rules and concrete operation semantics |
| `functions.py` | Callbacks for SQL builtins such as COALESCE, ROUND, EXTRACT, STRFTIME |
| `semantics.py` | Concrete carriers and validation |

`ZExpr` reevaluation accepts input names or parameter IDs and requires every
referenced input. It traverses the existing expression without rebuilding Terms;
CASE executes only the selected arm. Scalar function arguments are evaluated
before their callback runs.

`__and__` implements `&`, not Python's `and`. Likewise, `|` and `~` implement
SQL OR and NOT. These operators are eager and preserve both input expressions,
including NULL. Parenthesize comparisons: `(x > 0) & (y < 5)`. Python's `and`,
`or`, `not`, and chained comparisons invoke truth coercion, which raises to avoid
silently dropping an expression. Inspect `.concrete` for an explicit execution
decision; it does not record a symbolic branch.

`ZFloat` handles FLOAT and DECIMAL values; both use the Python float carrier.
DECIMAL casts round to the declared scale. Numeric promotion remains visible as
cast Terms. Integer
division truncates toward zero. Temporal values have no time zone.

Validation happens at runtime boundaries: inputs, replacement assignments, and
new scalar results. Terms enforce function signatures. Wrapping an already
computed result in a `ZValue` does not validate it again. Construct observations
through `Runtime` so those boundaries are respected.

This is the scalar foundation. Relational execution over database instances
lives in `parseval.instance`, branch coverage in `parseval.coverage`, and
solving in `parseval.smt`. STABLE functions such as CURRENT_TIMESTAMP use a
fixed reference date so that execution is repeatable. `Semantics` also holds
dialect policies: division by zero yielding NULL and lenient text-to-number
conversion (SQLite, MySQL).
