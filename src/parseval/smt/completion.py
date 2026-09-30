"""Conservative fresh-key completion for aggregate cardinality targets.

Keys used by observations or schema dependencies remain explicit. A weighted
class is only widened to distinct physical rows when its fresh integer keys
cannot affect the modeled fold. Whole-row multiplicity targets never opt in.
"""

from collections import Counter

import z3

from parseval.terms import terms as nodes
from parseval.terms.constraints import (
    CheckDecl, ForeignKeyDecl, GeneratedColumnDecl, PrimaryKeyDecl, UniqueDecl,
)
from parseval.terms.sorts import TypeKind
from parseval.coverage.observation import GroupCardinalityCondition
from parseval.coverage.witness import UnitWitnessPlan

from .values import UnsupportedEncodingError


def plan_completion(encoder, target, environment):
    obligation = target.obligation
    if (not obligation.conditions or not isinstance(obligation.plan, UnitWitnessPlan) or obligation.contexts
            or not all(isinstance(c, GroupCardinalityCondition) for c in obligation.conditions)):
        return {}
    if not any(
        isinstance(declaration, (PrimaryKeyDecl, UniqueDecl))
        and declaration.metadata.proof_active and len(declaration.columns) == 1
        for _, specification in encoder.database.context.relations()
        for declaration in specification.constraints
    ):
        return {}
    # CTEs and nested bag operators can hide repeated uses. Until lineage is
    # proved for them, use the exact complete-tuple representation.
    if obligation.relations:
        return {}
    roots = tuple(condition.term for condition in obligation.conditions)
    terms = tuple(encoder.arena.post_order(roots))
    if any(isinstance(encoder.arena[t], (nodes.LetRel, nodes.RelVar, nodes.Squash,
                                        nodes.UNot, nodes.Scalarize, nodes.InSubquery)) for t in terms):
        return {}
    scans = Counter()
    for term in terms:
        node = encoder.arena[term]
        if isinstance(node, nodes.At) and isinstance(encoder.arena[node.children[0]], nodes.Base):
            scans[encoder.arena[node.children[0]].payload.relation] += 1
    # Inspect argument dependencies separately from the target circuit. Group
    # size does not require an encoding of the aggregate operator itself (e.g.
    # STDDEV). Analysis must not add unused argument-definedness constraints to
    # the goal, nor hide key dependencies behind shared circuit definitions.
    from .encoding import UExprEncoder

    analysis = UExprEncoder(encoder.arena, encoder.database, budget=encoder.budget, prepared=encoder.prepared)
    try:
        for root in roots:
            fold = encoder.arena[root]
            source = encoder.bag(fold.children[0], environment)
            functions = [fold.children[1]] if isinstance(fold, nodes.GroupFold) else []
            for layout in fold.payload.calls:
                for child in (layout.argument_child, layout.filter_child):
                    if child is not None:
                        functions.append(fold.children[child])
            for function in functions:
                for entry in source:
                    analysis._apply(function, entry.row, environment)
    except UnsupportedEncodingError:
        return {}
    analysis.circuit.definitions.update(encoder.circuit.definitions)
    observed = analysis.circuit.dependencies((*encoder.observed_values, *analysis.observed_values))
    blocked = set()
    for relation, specification in encoder.database.context.relations():
        for declaration in specification.constraints:
            if not declaration.metadata.proof_active:
                continue
            if isinstance(declaration, (CheckDecl, GeneratedColumnDecl)):
                blocked.add(relation)
            if isinstance(declaration, ForeignKeyDecl):
                blocked.update((relation, declaration.target_relation))
    result = {}
    for relation, specification in encoder.database.context.relations():
        if relation in blocked or scans[relation] > 1:
            continue
        positions = set()
        for declaration in specification.constraints:
            if (not declaration.metadata.proof_active
                    or not isinstance(declaration, (PrimaryKeyDecl, UniqueDecl))
                    or len(declaration.columns) != 1):
                continue
            position = specification.column_position(declaration.columns[0])
            values = [entry.row.values[position] for entry in encoder.database.relations[relation]]
            if values and all(value.sql_type.kind is TypeKind.INTEGER
                              and value.value.get_id() not in observed
                              and (z3.is_false(value.is_null) or z3.is_true(value.is_null)
                                   or value.is_null.get_id() not in observed)
                              for value in values):
                positions.add(position)
        if positions:
            result[relation] = frozenset(positions)
    return result
