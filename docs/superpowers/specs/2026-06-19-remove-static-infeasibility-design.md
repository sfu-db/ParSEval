# Remove Static Infeasibility Heuristics

## Problem

`SymbolicEngine.generate()` calls `is_infeasible()` before compiling each
coverage target. The helper assumes every target addresses an entry in
`BranchNode.atoms`. Operator-level targets use `atom_id = -1`, and some nodes
such as scalar-subquery filters have no decomposed atoms. BIRD query 8 therefore
reaches `node.atoms[-1]` on an empty tuple and aborts generation.

The helper is also a schema-sensitive optimization rather than a soundness
boundary. It guesses that branches are impossible from predicate text and
nullability. The solver already decides whether generated constraints can be
satisfied.

## Decision

Remove the static infeasibility heuristic completely:

- delete `src/parseval/symbolic/infeasibility.py`;
- remove the pre-solver `is_infeasible()` call from `SymbolicEngine.generate()`;
- remove `is_infeasible` from `parseval.symbolic` exports;
- remove tests that directly exercise the deleted heuristic.

Do not add a compatibility shim or relocate these checks into target
construction.

## Retained Dynamic Exhaustion

Keep `BranchNode.infeasible`, `BranchTree.mark_infeasible()`, and all target
filtering based on that state. When solving or materialization fails, the
engine must continue marking that exact target infeasible. This is not static
guessing: it records the result of an attempted solver/materialization path and
prevents the generation loop from retrying the same target indefinitely.

## Data Flow

The generation loop becomes:

1. Select an uncovered coverage target.
2. Compile the complete target constraints.
3. Solve and materialize under an instance checkpoint.
4. On success, reevaluate coverage.
5. On failure, roll back and mark the target dynamically infeasible.

Every target shape, including operator-level targets and nodes with no atoms,
follows this same path.

## Testing

- Remove the two unit tests that call `is_infeasible()` directly.
- Retain tests proving dynamically marked targets disappear from uncovered and
  root-witness target lists.
- Add a fixture-independent regression for BIRD query 8:

```sql
SELECT NumTstTakr
FROM satscores
WHERE cds = (
  SELECT CDSCode
  FROM frpm
  ORDER BY `FRPM Count (K-12)` DESC
  LIMIT 1
)
```

The regression must run `instantiate_db()` against the minimal `satscores` and
`frpm` schema and assert generation does not fail with an atom-index error.

## Success Criteria

- No production or public-API reference to `is_infeasible` remains.
- Static predicate/schema heuristics do not run before constraint compilation.
- Solver/materialization failures still mark targets dynamically infeasible.
- BIRD query 8 completes generation without `IndexError`.
- Existing focused symbolic engine and BIRD query 247 regressions remain green.
