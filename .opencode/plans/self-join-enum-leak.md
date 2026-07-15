# Plan: Self-join temporal fix + enum/choices value-leak

## 1. Status of the four targeted query families

All four original pattern fixes are already implemented in the working tree (uncommitted):

| Family | Files changed | Status |
|--------|--------------|--------|
| CHAR_LENGTH (anonymous fn) | `src/parseval/plan/rex.py`, `src/parseval/solver/smt_translate.py` | done |
| NOT IN subquery | `src/parseval/symbolic/speculate.py`, `src/parseval/symbolic/constraints.py` | done |
| CROSS JOIN CASE (aggregate case_arm) | `src/parseval/symbolic/evaluator.py` | done |
| Self-join temporal | `src/parseval/symbolic/speculate.py` (`_derive_join` now forwards `join_data["condition"]`) | done |

The self-join `_derive_join` fix is **verified working**: with it, the solver now generates
`ACTIVITY_TYPE = 'START'` / `'END'` rows (previously none existed). The earlier
`e_s_<hex>` / `act_<hex>` leak is a *separate, pre-existing, general* bug that blocks
full verification.

## 2. The blocking issue: enum / choices value-leak

Concrete values such as `act_77714d` and `e_s_c16374` (and the baseline
`e_s_b4954c`) are **Z3/instance variable-name placeholders**, leaking into
materialized data. They come from `value_space.py:_text_hint` (line 285-295):

```python
def _text_hint(hint, length):
    raw = getattr(hint, "display", None) or str(hint)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    return f"{normalized[:budget]}_{digest}"[:length]   # -> "act_77714d"
```

This is reached **only when `ValueSpace.allowed is None`** — `ValueSpace.pick()`
(value_space.py:52-73) returns early from `allowed` (line 57-58) when it is set,
otherwise falls to `_pick_text` -> `_text_hint`.

### Root-cause chain (confirmed)

`compiler.py:160-190` is supposed to populate `allowed_values` from exactly three
sources, but **all three fail** for the dataset's enum shape:

1. `compiler.py:161` `profile.metadata.get("allowed_values")` — for the dataset
   schema `"ACTIVITY_TYPE": "ENUM,START,END"` (comma format, NOT a valid
   `exp.Enum`), the `TypeService().profile()` does not surface allowed_values.
2. `compiler.py:164` `spec.datatype.is_type(DataType.Type.ENUM)` — False for the
   comma-format dtype, so the ENUM-arg branch is skipped.
3. `compiler.py:180` `isinstance(check, ChoicesConstraint)` — **`ChoicesConstraint`
   (domain/constraints.py:56) is defined but never constructed anywhere** (grep:
   zero `ChoicesConstraint(` construction sites). The dataset constraint
   `{"in": [{"value": "ACTIVITY__ACTIVITY_TYPE"}, [{"literal": "START"}, {"literal": "END"}]]}`
   is therefore never turned into a `ChoicesConstraint`, so this branch never fires.

Result: `allowed_values` stays `None` -> `ValueSpace.allowed` stays `None`
(compiler.py:75-76 guard) -> `pick()` falls to `_pick_text` -> `_text_hint`
-> garbage `act_<hash>` / `e_s_<hash>` instead of `'START'` / `'END'`.

### Why this blocks the self-join family

The self-join query gold execution filters `ACTIVITY_TYPE = 'START'` / `'END'`.
Generated rows carry garbage `e_s_c16374` instead of real enum values, so the
join/cross-filter matches nothing -> 0-row result (or a `coerce_in`
`TypeCoercionError` during materialization, caught as a failed generation).

## 3. Fix direction (per user guidance)

> "if we do not have a correct constraint expression, we will not generate the
> value satisfy the constraints ... we should build proper constraint expressions passed to
> our solver, so that we will generate solutions."

i.e. the solver must **receive a real constraint expression** (`column IN (allowed_values)`)
so it generates a value that *satisfies* the enum — not rely on the domain
value-space fallback (`_text_hint`) that produced the placeholder.

### Fix A — propagate allowed_values into the ValueSpace (root cause)

Construct a `ChoicesConstraint` from the dataset `{"in": [...]}` constraint and/or
recognize the `ENUM,START,END` comma format as ENUM in `compiler.py`, so
`allowed_values` is populated and `ValueSpace.allowed` gets set (compiler.py:75-76).
Then `ValueSpace.pick()` (line 57-58) returns a valid enum value.

- Locate where dataset `{"in": ...}` constraints are parsed into `ColumnSpec.checks`
  and add `ChoicesConstraint(values=...)` construction there (currently missing — grep shows
  `ChoicesConstraint` is never constructed).
- Ensure `ENUM,START,END` is normalized to `exp.Enum('START','END')` before
  `compiler.py:164` (the DDL builder normalizes it; confirm the domain
  `TypeService().profile()` / `spec.datatype` path uses the same normalization).

### Fix B — emit an explicit `IN` constraint to the solver (user's preferred, more robust)

When a column has a finite allowed set, emit `column IN (allowed_values)` as a
constraint expression that flows through the *same* machinery that already works for
`WHERE ACTIVITY_TYPE = 'START'` — i.e. into the `BranchSpec` during
`speculate` and/or the `ConstraintGenerator` path. The solver then produces a
value in the allowed set, independent of the domain picker's fallback.

This is the more robust approach: it does not depend on `ValueSpace.allowed`
propagation and reuses the already-correct constraint-generation path.

### Revert speculative SMT edits

The earlier `src/parseval/solver/smt_solver.py` edits
(`_raw_payload_to_python` line 1166 `return str(payload)` -> `return None`;
`model.evaluate(..., model_completion=False)` at lines 275 & 1083) **do not fix the
leak** — the leak is in the **domain** value path (`value_space.py`), not SMT, and
the default `Solver` is the domain solver (`engine.py:128`). Those edits risk
regressions in the SMT path and should be **reverted**.

## 4. Verification

1. Revert `smt_solver.py` edits.
2. Implement Fix A and/or Fix B.
3. Re-run the self-join verification (dataset entry 19234, `ACTIVITY` self-join
   temporal query). Expect non-zero rows and no `act_` / `e_s_` placeholders in
   generated `ACTIVITY_TYPE` values.
4. Run the existing suite — confirm no new regressions beyond the 5 pre-existing
   `test_symbolic_engine.py` failures (confirmed identical on baseline via `git stash`).
5. Re-run the full `tests/experiment/test_mysql.py` experiment to confirm the
   self-join-temporal (and other enum-bearing) families move out of the 0-row bucket.
