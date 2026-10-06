"""Concrete query results for dataset replay, computed by the concolic machine."""
from parseval.instance import Machine, Sequence, Valuation
from parseval.parser.query import lower_query
from parseval.uexpr.lowering import UExprCompiler


def result(sql, catalog, instance):
    """The rows ``sql`` returns on ``instance`` and whether they are ordered."""
    query = lower_query(sql, catalog)
    root = UExprCompiler(query.arena, instance.arena).compile(query.root).simplified_root
    valuation = Valuation(instance)
    machine = Machine(valuation)
    relation = machine.run(root).relation
    rows = machine.occurrences(relation)
    values = tuple(tuple(valuation.concrete(cell) for cell in row.cells) for row in rows)
    return values, isinstance(relation, Sequence)
