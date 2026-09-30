"""Per-relation support planning; weights do not consume support slots."""

from parseval.terms import terms as nodes
from parseval.terms.constraints import ForeignKeyDecl
from parseval.terms.walk import post_order
from parseval.uexpr.witness import UnitWitnessPlan
from parseval.uexpr.observation import GroupCardinalityCondition


def target_roots(target):
    obligation = target.obligation
    roots = [target.site.term, *obligation.relations]
    roots.extend(condition.term for condition in obligation.conditions)
    plans = list(obligation.contexts)
    if not isinstance(obligation.plan, UnitWitnessPlan):
        plans.append(obligation.plan)
    for plan in plans:
        roots.append(plan.product.term)
        for binding in plan.bindings:
            for step in binding.steps:
                roots.append(step.source if hasattr(step, "source") else step.expression)
    return tuple(roots)


def support_bounds(catalog, arena, target, seed, max_support):
    required = {
        arena[term].payload.relation
        for term in post_order(arena, target_roots(target))
        if isinstance(arena[term], nodes.Base)
    }
    # Referenced parents can themselves have outgoing foreign keys.
    while True:
        parents = {
            declaration.target_relation
            for relation in required
            for declaration in catalog.context.relation(relation).constraints
            if isinstance(declaration, ForeignKeyDecl) and declaration.metadata.proof_active
        }
        if parents <= required:
            break
        required |= parents
    lower = {}
    upper = {}
    cardinality_only = all(isinstance(condition, GroupCardinalityCondition)
                           for condition in target.obligation.conditions)
    for relation, _ in catalog.context.relations():
        unique = []
        for row in (() if seed is None else seed.rows(relation)):
            if row.values not in unique:
                unique.append(row.values)
        # Seeds are preferences, not extra support requirements. In particular,
        # fresh keys from a large materialized group must not turn its weight
        # back into hundreds of symbolic tuple slots on the next attempt.
        seeded = 0 if cardinality_only else min(len(unique), max_support)
        lower[relation] = max(seeded, int(relation in required))
        upper[relation] = max_support if relation in required or seeded else 0
    return lower, upper


def grow_support(current, upper):
    return {relation: min(upper[relation], max(count + 1, count * 2))
            if count < upper[relation] else count
            for relation, count in current.items()}
