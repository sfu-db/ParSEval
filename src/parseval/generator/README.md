# Concolic generation

`generate(sql, catalog, config=GenerationConfig(...))` grows one database
whose execution covers the query's branch outcomes. `result.instance` is the
final database, `result.nonempty` tells whether the query output is
productive, and `result.coverage` lists reached and covered outcomes and the
solver status of the ones that failed.

```python
from parseval import Catalog, GenerationConfig, generate

catalog = Catalog.from_ddl("CREATE TABLE t(id INT PRIMARY KEY, g INT, x INT)")
result = generate("SELECT g, COUNT(x) FROM t WHERE x > 3 GROUP BY g", catalog)
print(result.nonempty, result.instance)
print(result.coverage.ratio, result.coverage.failed)
```

Generation starts from rows sampled by `parseval.speculate` from the
query's compact IR, so solving is left to the outcomes speculation missed
(`GenerationConfig(speculate=False)` starts from an empty database).

Each round executes the query on the current instance, which holds
candidate rows (multiplicity zero) per relation: one to start, more when a
target needs them. The first target is
productive output; then uncovered outcomes in site order. A solve asks for
the target's witness, the integrity of the candidate rows it uses and, once
the output is productive, the witnesses of covered outcomes that depend on
candidate rows. Accepted values append rows; stored rows never change. The
new instance is executed again and accepted only if it covers the target and
loses no covered outcome.

Each uncovered outcome is solved at most once per database version and
budget, and a version only follows a solve that covers a new outcome, so
generation ends without row or attempt limits; `time_limit_s` stops it
earlier with the latest accepted version. Until the output is productive,
execution considers every combination of candidate rows; afterwards a binding
may use one candidate row, and two once no outcome is left to try (`BUDGET`,
`MAX_BUDGET` in `generate.py`, bounds on symbolic work rather than settings).
An UNSAT target whose kept conditions were capped is solved once more with
all of them. A target that stays UNSAT is
solved once more with candidate rows allowed to repeat while each prefers to
occur once; the multiplicities of that model are each relation's demand for
rows (for example to pass an OFFSET or reach `HAVING COUNT(*) > 20`).
Relations whose demand exceeds their candidate rows grow and the target is
solved again. Repetition only adds rows equal to existing ones; when it cannot
satisfy the target, one more row is tried in each relation the target involves
(`x > (SELECT AVG(x) ...)` needs a second, different row). Growth ends when
neither helps. No row limit applies.

`on_instance` receives every accepted database version; the last call is
the final database, so an interrupted run still leaves its latest version.
Dataset experiments, SQLite export and PostgreSQL replay live in
`scripts/experiments/`.
