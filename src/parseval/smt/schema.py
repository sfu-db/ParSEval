"""Integrity constraints over weighted support."""
from __future__ import annotations
from typing import cast
import z3
from parseval.terms.constraints import (
    CheckDecl, ForeignKeyDecl, ForeignKeyMatch, GeneratedColumnDecl,
    NotNullDecl, NullConflictPolicy, PrimaryKeyDecl, UniqueDecl,
)
from .values import (_sum, FALSE, SymbolicValue, UnsupportedEncodingError,
    _value_equal, _nullable_value_equal, _row_equal, _value_domain)

def schema_constraints(encoder) -> tuple[z3.BoolRef, ...]:
    from .encoding import UExprEncoder, _Environment

    constraints: list[z3.BoolRef] = []
    catalog_encoder = UExprEncoder(
        encoder.database.catalog.constraint_arena,
        encoder.database,
        budget=encoder.budget,
    )
    for relation, specification in encoder.arena.context.relations():
        entries = encoder.database.relations[relation]
        positions = {
            column.id: index for index, column in enumerate(specification.columns)
        }
        for declaration in specification.constraints:
            encoder.budget.tick()
            if not declaration.metadata.proof_active:
                continue
            if isinstance(declaration, NotNullDecl):
                position = positions[declaration.column]
                constraints.extend(
                    z3.Implies(entry.active, z3.Not(entry.row.values[position].is_null))
                    for entry in entries
                )
            elif isinstance(declaration, (PrimaryKeyDecl, UniqueDecl)):
                key = tuple(positions[column] for column in declaration.columns)
                if isinstance(declaration, PrimaryKeyDecl):
                    constraints.extend(
                        z3.Implies(
                            entry.active,
                            z3.And(*(z3.Not(entry.row.values[index].is_null) for index in key)),
                        )
                        for entry in entries
                    )
                if any(position in encoder.database.completion.get(relation, ()) for position in key):
                    continue
                for left_index, left in enumerate(entries):
                    comparable_self = (
                        z3.BoolVal(True)
                        if declaration.null_policy
                        is NullConflictPolicy.NULLS_NOT_DISTINCT
                        else z3.And(
                            *(z3.Not(left.row.values[index].is_null) for index in key)
                        )
                    )
                    constraints.append(
                        z3.Implies(
                            z3.And(left.active, comparable_self),
                            left.multiplicity <= 1,
                        )
                    )
                    for right in entries[left_index + 1 :]:
                        nulls_not_distinct = (
                            declaration.null_policy
                            is NullConflictPolicy.NULLS_NOT_DISTINCT
                        )
                        comparable = (
                            z3.BoolVal(True)
                            if nulls_not_distinct
                            else z3.And(
                                *(z3.Not(left.row.values[index].is_null) for index in key),
                                *(z3.Not(right.row.values[index].is_null) for index in key),
                            )
                        )
                        different = z3.Or(
                            *(
                                z3.Not(
                                    z3.Or(
                                        z3.And(
                                            left.row.values[index].is_null,
                                            right.row.values[index].is_null,
                                        ),
                                        _value_equal(
                                            left.row.values[index],
                                            right.row.values[index],
                                        ),
                                    )
                                )
                                for index in key
                            )
                        )
                        constraints.append(
                            z3.Implies(z3.And(left.active, right.active, comparable), different)
                        )
            elif isinstance(declaration, ForeignKeyDecl):
                source_positions = tuple(positions[column] for column in declaration.source)
                target_specification = encoder.arena.context.relation(declaration.target_relation)
                target_positions = tuple(
                    target_specification.column_position(column)
                    for column in declaration.target
                )
                targets = encoder.database.relations[declaration.target_relation]
                for source in entries:
                    values = tuple(source.row.values[index] for index in source_positions)
                    null_count = _sum(*(z3.If(value.is_null, 1, 0) for value in values))
                    if declaration.match is ForeignKeyMatch.FULL:
                        exempt = null_count == len(values)
                        constraints.append(
                            z3.Implies(
                                source.active,
                                z3.Or(null_count == 0, exempt),
                            )
                        )
                    elif declaration.match is ForeignKeyMatch.SIMPLE:
                        exempt = null_count > 0
                    else:
                        raise UnsupportedEncodingError("foreign-key-match:partial")
                    match = z3.Or(
                        *(
                            z3.And(
                                target.active,
                                *(
                                    _value_equal(value, target.row.values[position])
                                    for value, position in zip(values, target_positions, strict=True)
                                ),
                            )
                            for target in targets
                        )
                    )
                    constraints.append(
                        z3.Implies(source.active, z3.Or(exempt, match))
                    )
            elif isinstance(declaration, CheckDecl):
                function = catalog_encoder.arena[declaration.predicate.term]
                for entry in entries:
                    predicate = catalog_encoder.predicate(
                        function.children[0],
                        _Environment((entry.row,)),
                    )
                    constraints.append(
                        z3.Implies(entry.active, predicate != FALSE)
                    )
            elif isinstance(declaration, GeneratedColumnDecl):
                function = catalog_encoder.arena[declaration.expression.term]
                position = positions[declaration.column]
                for entry in entries:
                    expected = cast(
                        SymbolicValue,
                        catalog_encoder.value(
                            function.children[0],
                            _Environment((entry.row,)),
                        ),
                    )
                    constraints.append(
                        z3.Implies(
                            entry.active,
                            _nullable_value_equal(
                                entry.row.values[position], expected
                            ),
                        )
                    )
    for entries in encoder.database.relations.values():
        constraints.extend(entry.multiplicity >= 0 for entry in entries)
        constraints.extend(
            z3.Implies(entries[index].active, entries[index - 1].active)
            for index in range(1, len(entries))
        )
        for index, entry in enumerate(entries):
            constraints.extend(
                z3.Implies(
                    z3.And(entry.active, previous.active),
                    z3.Not(_row_equal(entry.row, previous.row)),
                )
                for previous in entries[:index]
            )
        for entry in entries:
            for value in entry.row.values:
                domain = _value_domain(value)
                if domain is not None:
                    constraints.append(
                        z3.Implies(
                            z3.And(entry.active, z3.Not(value.is_null)),
                            domain,
                        )
                    )
    constraints.extend(catalog_encoder.constraints)
    return tuple(constraints)
