# Coverage-directed generation

Generation is split into small, replaceable responsibilities:

- `GenerationConfig` defines exploration and SMT budgets.
- `CoverageExplorer` observes one concrete instance.
- `CoverageTracker` owns target identity and outcome reporting.
- `CoverageFrontier` schedules newly discovered targets deterministically.
- `Generator` coordinates compilation, solving, concrete replay, and further
  discovery.
- `generate` is the convenience facade.

The SMT solver is a bounded feasibility backend. A SAT result becomes coverage
only after schema validation and independent concrete replay. Exhausting the
configured support is reported as `bounded_unsat`; it is never promoted to a
proof that no database can cover the target. Timeouts and unsupported encoding
are retained as distinct outcomes.

```python
from parseval import Catalog, GenerationConfig, generate

catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
result = generate(
    "SELECT a FROM t WHERE a > 0",
    catalog,
    config=GenerationConfig(max_attempts=20, timeout_ms=1_000),
)

for case in result.counterexamples:
    print(case.instance, case.covered)
print(result.coverage.ratio, result.coverage.not_attempted)
```
