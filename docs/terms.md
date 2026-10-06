# Terms

This module represents typed SQL expressions, relational operators, and
U-expressions. It requires Python 3.10 or newer. SQLGlot parsing, name resolution,
coercion, and relational-to-U-expression lowering belong outside this module.

## Construction

Use `Context` for semantic declarations and context-local ID allocation, `TermArena` for immutable shared nodes,
and `IRBuilder` for constructing expressions. A `RowShape` contains `ScalarSort`
fields; SQL column names belong to `parseval.catalog` metadata.
`terms.decls` also contains `ColumnSpec` and `RelationSpec`; typed integrity
constraints live in `terms.constraints`. Surface SQL identifiers live in
`parseval.identifiers`. See [catalog construction](parser.md). Declaration IDs are local
to a context, and term IDs are local to an arena.

`ScalarType` carries the value kind and decimal precision/scale. Catalog column
bindings also use it to hold `max_length` and `integer_bits` storage limits,
resolved by `SQLDialect` at registration. SMT schema constraints and concrete
validation consume that internal type directly. Expression sorts use its
`value_type`, which omits column storage limits so computed values can exceed
the size of their inputs.

```python
from parseval.terms import Context, IRBuilder, RowShape, TermArena, verify_uexpr
from parseval.terms.decls import ColumnSpec, RelationSpec
from parseval.terms.names import ColumnId, RelationId
from parseval.terms.sorts import INTEGER, RowSort, ScalarSort

context = Context()
field = ScalarSort(INTEGER)
schema = context.intern_schema(RowShape((field,)))
context.register_relation(
    RelationId(0),
    RelationSpec(schema, (ColumnSpec(ColumnId(0), field),)),
)
arena = TermArena(context)
b = IRBuilder(arena)
relation = b.base(RelationId(0))

# SELECT x FROM R WHERE x = 7
root = b.finish(
    b.bag_lam(schema, lambda output:
        b.sum(RowSort(schema), lambda row:
            b.mul(
                b.at(relation, row),
                b.indicator(b.eq3(b.field(row, 0), b.literal(7, INTEGER))),
                b.indicator(b.row_identity_eq(output, row)),
            )
        )
    )
)
verify_uexpr(arena, root)
print(arena.view(root))
```

Builder callbacks track lexical scope. Use their scoped handles directly when
referring to outer rows or relations; resolving an open handle manually discards
that scope information. Row and relation variables have separate De Bruijn
namespaces. Substitution shifts both to avoid capture.

## Invariants

- `intern_checked` validates payloads, arity, operand sorts, and arena ownership
  before applying local normalization. It resolves child nodes once. The builder
  and `rebuild` use the same construction path.
- Addition and multiplication flatten, order operands within the arena, and
  remove identities. Multiplication absorbs zero. Duplicate operands remain:
  bag multiplicities are not sets.
- SQL conjunction/disjunction simplify their identities and absorbing constants.
  Indicators map TRUE to one and FALSE/UNKNOWN to zero; conjunction becomes
  multiplication, and disjunction becomes squashed addition. This is local
  normalization, not a complete SQL equivalence decision procedure.
- SQL Boolean values and three-valued predicates are connected explicitly by
  `to_predicate` and `to_boolean`. NULL corresponds to UNKNOWN. `to_boolean`
  conservatively returns a nullable Boolean. SQL `not3` is distinct from `unot`.
- `finish` checks that no variables escape their binders. `verify_uexpr` also
  rejects compact operators such as `Filter` and `Map` that still require lowering.
  Aggregate and ordering extensions are included in that verification.
- Storage is append-only. There is no rollback or ID reuse. Relation replacement
  can update constraints, but cannot change its schema or column metadata.

Node records are internal representations, not checked constructors. Use the
builder or arena to obtain term IDs. Hash-consing establishes structural sharing;
it does not by itself define evaluation or effect handling for volatile functions.

Run the module's regression tests from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests/terms -v
```
