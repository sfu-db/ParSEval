# Solver Module Reference

`parseval.solver` is ParSEval's pure constraint-solving engine. It accepts typed
`sqlglot` constraint expressions and returns concrete Python values that satisfy
the full formula, or a reason why no sound assignment was produced.

The solver does not read an `Instance` or database schema. Callers must annotate
every `exp.Column.type` before invoking the solver.

---

## Public API

```python
from parseval.solver import Solver, SolverConstraint, SolveResult
```

### `SolverConstraint`

```python
@dataclass
class SolverConstraint:
    target_tables: Tuple[str, ...]
    constraints: List[exp.Expression] = field(default_factory=list)
    join_equalities: List[Tuple[str, str, str, str]] = field(default_factory=list)
    alias_map: Dict[str, str] = field(default_factory=dict)
```

- `target_tables` are the table keys to generate assignments for. For aliased
  queries, these are usually alias names.
- `constraints` are SQL predicates represented as `sqlglot` AST nodes.
- `join_equalities` are explicit column equalities in the form
  `(left_table, left_col, right_table, right_col)`.
- `alias_map` maps alias keys to physical table names.

### `SolveResult`

```python
@dataclass
class SolveResult:
    sat: bool
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reason: str = ""
```

When `sat=True`, `assignments` contains table or alias keys mapped to generated
column values. When `sat=False`, `reason` describes the failure mode.

### Usage

```python
solver = Solver(dialect="sqlite", timeout_ms=5000)
result = solver.solve(constraint)

if result.sat:
    rows = result.assignments
else:
    raise ValueError(result.reason)
```

---

## Solver Flow

ParSEval uses a two-tier solver:

1. `DomainSolver` is the sound fast path. It analyzes the whole formula and
   returns one of:
   - `sat`: the domain tier handled the full formula and produced assignments.
   - `unsat`: the domain tier proved the constraints contradictory.
   - `unknown`: the domain tier cannot soundly handle the full formula.
2. `SMTSolver` runs only when the domain tier returns `unknown`.

The unified `Solver` short-circuits on domain `unsat`, trusts domain `sat`, and
falls back to SMT only for domain `unknown`.

The SMT fallback is strict. If SQL-to-SMT translation is incomplete, the solver
returns `SolveResult(sat=False, reason="unsupported_smt_expression")` instead
of solving a subset of the input constraints.

```
Solver.solve(constraint)
 │
 ├─ validate column type annotations
 │
 ├─ DomainSolver.solve(...)
 │    ├─ sat     → return assignments
 │    ├─ unsat   → return unsat without calling SMT
 │    └─ unknown → retry full original constraints in SMT
 │
 └─ SMTSolver
      ├─ all expressions translated and Z3 says sat → return assignments
      └─ unsupported translation / unsat / unknown  → return failure reason
```

---

## Domain Solver

`DomainSolver` performs CSP-lite value-space narrowing over supported predicate
forms: literal comparisons, `IS NULL`, `IS NOT NULL`, `LIKE`, `IN`, `BETWEEN`,
column-column equality, and explicit join equalities.

It returns `DomainResult`:

```python
@dataclass
class DomainResult:
    status: str  # "sat", "unsat", or "unknown"
    assignments: Optional[Dict[str, Dict[str, Any]]] = None
    reason: str = ""
```

The domain tier is conservative. Unsupported arithmetic, unsupported `NOT`, and
ambiguous `OR` formulas return `unknown` rather than partial assignments. This
keeps domain solving a sound fast path: `sat` means all constraints were handled,
not merely the supported subset.

Domain assignments remain in alias namespace. The unified solver remaps aliases
to physical table names only when the remap is lossless.

---

## SMT Solver

`SMTSolver` is the Z3-backed fallback for formulas outside the domain tier, such
as arithmetic relationships and complex cross-column constraints. It receives
the original full constraint list, not residual hints from the domain solver.

SMT variables are declared from all columns found in constraints and join
equalities. Join equalities are added directly to the Z3 solver after table names
are resolved into the SMT variable namespace.

Unsupported SMT translation is fail-closed. If any expression cannot be
translated, unified solving returns `unsupported_smt_expression`; it does not
drop that expression and continue.

---

## Alias And Self-Join Behavior

The domain tier keeps assignment keys alias-qualified so self-joins can produce
independent rows:

```python
SolverConstraint(
    target_tables=("a", "b"),
    alias_map={"a": "people", "b": "people"},
)
```

For non-ambiguous aliases, unified solving remaps result keys back to physical
table names. For physical self-joins where multiple aliases map to the same
table, the solver preserves aliases because collapsing them would lose row
identity.

SMT join equalities may be written in alias form or unambiguous physical-table
form. Ambiguous physical self-join equalities fail closed rather than guessing
which alias was intended.
