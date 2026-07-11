# Design: `create_rows` + Instance/Domain boundary

Date: 2026-07-10  
Status: approved for planning (pending user review of this file)

## Goal

Make `Instance.create_rows` (and `create_row`) produce **one full constraint-valid row per listed batch entry**, filling missing columns via `DomainGenerator`, while keeping a **hard boundary** between Instance and Domain.

## Decisions (locked)

- **Explicit tables only:** only tables present in the `concretes` mapping are requested; unlisted tables stay empty unless created as FK parents.
- **Empty payload = one generated row:** `{}` and `[]` both normalize to a single empty preset map `{}`, then domain completes all columns.
- **Partial presets:** caller-provided concrete values are locked; domain fills the rest and enforces uniqueness / CHECK / FK binding against existing + parent rows.
- **No whole-schema fill** when `concretes` is `None` or `{}` — return `{}` and create nothing.
- **No count-based N-row API** in this pass.
- **No CSP search** in this pass — greedy `DomainGenerator` only.

## Module boundary

```text
Instance                          Domain
--------                          ------
owns rows / Variables             owns ValueSpace / ColumnDomainPlan
checkpoint / snapshot / to_db     compile column constraints → space
FK *row* recursion + cycles       pick / narrow / freshen / CHECK eval
calls DomainGenerator             complete_row / next_value (stateless)
place_row(completed values)       never mutates Instance
```

### Instance owns

- Schema handle (`InstanceSchema`), row store, symbols, bootstrap cycle bookkeeping.
- Orchestration: normalize presets → ensure missing **parent rows exist** → call `complete_row` → `place_row`.
- Mapping domain errors to Instance exception types where needed.
- Batch ordering among **listed** tables (`_creation_order`).

### Domain owns

- Inventing concrete cell values from `InstanceSchema` + presets + existing/parent row maps.
- FK **value** binding (copy parent key into child cells when parents are provided).
- Single-column and composite uniqueness freshening.
- NOT NULL / type-family defaults via `ValueSpace.pick`.
- Supported CHECK evaluation on the completed row dict.
- Raising domain exceptions on conflict / unsupported CHECK / illegal NULL.

### Forbidden crossings

- Instance must **not** invent type defaults, freshen uniqueness, or pick FK cell values.
- Domain must **not** create/store rows, recurse into parent table creation, or hold Instance state.
- Domain receives only plain `Mapping` row dicts (and schema); Instance converts `Row`/`Variable` ↔ dicts at the boundary.

## `create_rows` behavior

```python
def create_rows(
    self,
    concretes: Mapping[
        exp.Table | str,
        Mapping[exp.Identifier | str, Sequence[Any]]
        | Sequence[Mapping[exp.Identifier | str, Any]],
    ] | None = None,
) -> Dict[str, List[RowCreationResult]]:
```

| Input for a table | Normalized batch | Result |
|-------------------|------------------|--------|
| `{}` | `[{}]` | one fully domain-completed row |
| `[]` | `[{}]` | same |
| `[{"id": 1}]` | one partial preset | domain fills other columns |
| `{"id": [1, 2]}` | two partial presets | two rows, each completed |
| table omitted | — | no rows for that table |
| `concretes is None` or `{}` | no tables | return `{}` |

Per entry: `create_row(table, presets)` which:

1. Rejects illegal explicit NULLs (via domain when materializing).
2. `_ensure_fk_parents`: if FK sources are fully set, ensure a matching parent row exists (create parent with those key values if missing); if parents are empty and FK unset, create one parent row (domain fills parent); does **not** invent child FK values.
3. `_materialize_row`: `DomainGenerator.complete_row(presets, existing_rows, parent_rows, locked=preset keys)` then `place_row`.
4. Returns `RowCreationResult` including any auto-created parents.

## Constraint guarantees

After a successful `create_row` / `create_rows` entry, the placed row must satisfy:

| Constraint | Enforced by |
|------------|-------------|
| Column coverage (every column has a cell) | Domain `complete_row` return shape; Instance `place_row` builds Variables for all columns |
| NOT NULL (non-nullable) | Domain presets + fill |
| UNIQUE / PRIMARY KEY (single + composite) | Domain freshen against `existing_rows` |
| FOREIGN KEY (referential) | Instance ensures parent **rows**; Domain binds child FK **values** to an existing parent map |
| CHECK (supported) | Domain validates full row; unsupported CHECK → error (fail closed) |

Circular FKs: Instance bootstrap bookkeeping shares key values across the cycle; Domain still fills non-bootstrap columns.

## Error handling

- Domain `UniqueConflictError` / `ConstraintViolationError` → Instance equivalents (or re-raise mapped).
- Preset that cannot be satisfied (locked duplicate unique, FK pointing at missing parent that cannot be created) → hard error; do not silently drop constraints.

## Testing

- Empty `{}` / `[]` for a listed table → one row; all columns concrete; PK unique across two calls.
- Child-only `create_rows({"child": {}})` → parent row auto-created; child FK matches parent key.
- Partial presets + composite uniqueness across batch entries.
- Instance unit tests assert no type-default / unique-freshen helpers remain on `Instance`.
- Existing Instance + domain suites stay green.

## Out of scope

- CSP solver / backtracking search.
- Whole-database `create_rows()` fill.
- Revising symbolic engine call sites beyond what breaks from API semantics.
- Rewriting `docs/domain.md` legacy narrative (optional follow-up).

## Success criteria

1. Listed empty payloads yield one constraint-valid full row each.
2. Instance never invents cell values; Domain never mutates Instance.
3. FK + composite unique + CHECK hold for generated rows in unit tests.
4. Clear docstring on `create_rows` / `create_row` stating the boundary and empty-payload semantics.
