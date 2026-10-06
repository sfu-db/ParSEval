"""Query-level evidence, separate from populated tables and branch coverage."""
from collections import Counter

from parseval.instance import Instance

from .execution import result


def corpus_outcomes(query, catalog, instances):
    def signature(instance):
        rows, ordered = result(query, catalog, instance)
        return rows if ordered else Counter(rows)

    empty = signature(Instance(catalog))
    evidence = []
    for instance in instances:
        outcome = signature(instance)
        count = sum(outcome.values()) if isinstance(outcome, Counter) else len(outcome)
        evidence.append({'input_rows': instance.row_count, 'output_rows': count,
                         'changes_empty_result': outcome != empty})
    return evidence
