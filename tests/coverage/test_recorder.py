"""Branch outcomes recorded during concolic execution."""

from parseval.catalog import Catalog
from parseval.coverage import Recorder, Sites
from parseval.instance import Instance, Machine, Valuation
from parseval.parser.query import lower_query
from parseval.uexpr.lowering import UExprCompiler

DDL = "CREATE TABLE t(id INT PRIMARY KEY, x INT); CREATE TABLE s(id INT PRIMARY KEY, tid INT, y INT)"


def record(sql, rows, candidates=()):
    catalog = Catalog.from_ddl(DDL)
    instance = Instance(catalog)
    open_inputs = set()
    for table, values, multiplicity in [*((t, r, 1) for t, r in rows), *((t, r, 0) for t, r in candidates)]:
        instance, slot = instance.insert(catalog.resolve_table(table).relation, values, multiplicity)
        if not multiplicity:
            open_inputs.update(slot.parameters)
    query = lower_query(sql, catalog)
    root = UExprCompiler(query.arena, instance.arena).compile(query.root).simplified_root
    sites = Sites()
    valuation = Valuation(instance, frozenset(open_inputs))
    recorder = Recorder(sites, valuation)
    Machine(valuation, recorder).run(root)
    return {(instance.arena[sites.terms[target.site]].key, target.outcome) for target in recorder.coverage.covered}, recorder.coverage


def test_predicate_outcomes_are_covered_by_any_stored_row():
    covered, coverage = record("SELECT x FROM t WHERE x > 3", [("t", (1, 5)), ("t", (2, None))])
    assert {("predicate.lt3", "true"), ("predicate.lt3", "unknown")} <= covered
    assert ("predicate.lt3", "false") not in covered
    assert not coverage.candidates


def test_candidate_rows_give_symbolic_witnesses():
    covered, coverage = record("SELECT x FROM t WHERE x > 3", [("t", (1, 5))], [("t", (2, 0))])
    assert ("predicate.lt3", "false") not in covered
    assert any(witnesses for target, witnesses in coverage.candidates.items() if target.outcome == "false")


def test_correlated_absence_is_a_branch():
    sql = "SELECT t.x FROM t WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.tid = t.id)"
    covered, _ = record(sql, [("t", (1, 5)), ("t", (2, 6)), ("s", (1, 1, 0))])
    outcomes = {outcome for key, outcome in covered if key == "subquery.scalarize"}
    assert {"empty", "nonempty"} <= outcomes


def test_projection_and_group_values():
    covered, _ = record(
        "SELECT x, COUNT(*) FROM t GROUP BY x",
        [("t", (1, 5)), ("t", (2, 5)), ("t", (3, None))],
    )
    assert ("bag.lambda", "null") in covered
    assert ("bag.lambda", "distinct") in covered
    assert ("agg.group_fold", "multiple") in covered
