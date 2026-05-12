## Comprehensive Plan: Domain-Based Constraint Solving

### Where It Lives

The domain-based solver is not a separate module — it's the new Tier 1 inside solver/unified.py, replacing the current ad-hoc heuristic functions. The architecture becomes:

solver/
├── __init__.py      # exports: Solver, SolveResult
├── unified.py       # Solver class (orchestrator)
├── domain_solver.py # NEW: Domain + CSP-lite constraint solving
└── smt.py           # Z3 backend (Tier 2, unchanged)


### The Three Tiers (Revised)

Tier 0: Trivial (unchanged)
  - Single predicate, single column, direct assignment
  - col = literal → assign literal
  - col IS NULL → assign None
  - Zero computation cost

Tier 1: Domain-based CSP (NEW — replaces current heuristic)
  - Multiple predicates on multiple columns
  - Builds domains, narrows them, resolves dependencies
  - Handles: range intersections, NOT IN avoidance, JOIN coordination,
    derived values (b = a + 1), compound predicates
  - Cost: O(n × m) where n = variables, m = constraints

Tier 2: SMT / Z3 (unchanged)
  - Anything Tier 1 can't handle (non-linear arithmetic, complex string
    constraints, quantifiers)
  - Cost: exponential worst case, but rare in practice


### Integration Points

The domain solver is called from two places:

1. Solver.solve(constraint) — when the symbolic engine targets a specific atom. The constraint carries path_predicates, join_equalities, not_null_columns, avoid_values. All
of these become domain narrowing inputs.

2. Resolver.resolve(spec) in speculate.py — when materializing a speculative row set. The TableRequirement's predicates, fixed_values, shared_keys, not_null, must_null all 
map directly to domain constraints.

Both call the same DomainSolver.solve(variables, constraints) API.

### The Domain Model

python
@dataclass
class Domain:
    """The set of valid values for one column."""
    family: TypeFamily  # INT / REAL / TEXT / BOOL / DATE / DATETIME
    
    # Bounds (for ordered types: INT, REAL, DATE, DATETIME)
    min_val: Optional[Any] = None   # inclusive lower bound
    max_val: Optional[Any] = None   # inclusive upper bound
    
    # Exact value (singleton domain)
    equals: Optional[Any] = None
    
    # Exclusions
    not_equals: Set[Any] = field(default_factory=set)
    
    # Enumerated domain (from IN (...) or ENUM types)
    allowed: Optional[Set[Any]] = None
    
    # NULL handling
    must_null: bool = False
    not_null: bool = False
    
    # String constraints
    like_pattern: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    
    # Derived (value depends on another variable)
    derived_from: Optional[Tuple[str, str, Any]] = None
    # (source_var_name, operator, operand) e.g. ("a", "+", 1) means this = a + 1


### The Constraint Graph

python
@dataclass
class CSPVariable:
    """A column that needs a value."""
    name: str          # "table.column"
    domain: Domain
    assigned: Optional[Any] = None

@dataclass  
class CSPConstraint:
    """A relationship between variables."""
    variables: List[str]  # variable names involved
    kind: str             # "eq" / "gt" / "lt" / "derived" / "shared"
    params: Dict[str, Any] = field(default_factory=dict)


### The Algorithm (3 phases)

Phase 1: BUILD
  Input: SolverConstraint (from symbolic engine) or TableRequirement (from speculate)
  Output: List[CSPVariable], List[CSPConstraint]
  
  For each predicate in the constraint set:
    - Identify which columns are involved
    - Create/update CSPVariable with domain narrowing
    - Create CSPConstraint linking variables

Phase 2: PROPAGATE (AC-3 lite)
  Repeat until fixed point:
    For each constraint:
      Narrow domains of involved variables based on the constraint.
      If any domain becomes empty → return UNSAT.
  
  Key narrowing rules:
    - col > X → domain.min_val = max(domain.min_val, X + 1)
    - col < X → domain.max_val = min(domain.max_val, X - 1)
    - col = X → domain.equals = X
    - col != X → domain.not_equals.add(X)
    - col IN S → domain.allowed = S ∩ domain.allowed
    - a = b → unify domains (intersection)
    - b = a + 1 → mark b as derived from a

Phase 3: ASSIGN
  Topological sort variables by dependency (derived vars last).
  For each variable:
    If derived: compute from source variable's assignment.
    Else: pick a value from domain (prefer middle of range for robustness).
  
  If conflict detected (picked value violates a constraint):
    Backtrack one step, try next value in domain.
    Max backtrack depth: 3 (SQL predicates rarely need more).


### How It Integrates with the Speculative Component

The speculate's Resolver._build_row currently does:
python
row = {}
row.update(fixed_values)
for col, key_id in shared_keys: row[col] = shared[key_id]
for col, op, val in predicates: row[col] = satisfy(op, val)


With the domain solver, it becomes:
python
variables, constraints = build_csp(table, requirement, shared_values)
solution = domain_solver.solve(variables, constraints)
row = {var.column: var.assigned for var in solution}


This means the Resolver doesn't need its own _satisfy logic — it delegates entirely to the domain solver. The domain solver handles all the interactions correctly.

### How It Integrates with the Unified Solver

The Solver.solve(constraint) currently does:
python
result = _try_trivial(atom, ...)
if not result: result = _try_heuristic(atom, ...)
if not result: result = _try_smt(...)


With the domain solver:
python
# Build CSP from ALL constraints (atom + path + join + FK + unique)
variables, constraints = build_csp_from_solver_constraint(constraint)

# Phase 1: Try domain solving
solution = domain_solver.solve(variables, constraints)
if solution is not None:
    return SolveResult(sat=True, assignments=solution)

# Phase 2: Fall back to Z3
return _try_smt(constraint, ...)


The domain solver subsumes both Tier 0 (trivial) and Tier 1 (heuristic) — a single col = 5 is just a CSP with one variable and a singleton domain.

### What This Fixes

| Current failure | Why it fails | Domain solver fix |
|---|---|---|
| a > 5 AND a < 10 | Tier 1 picks 6 then 9 | Domain: {6..9}, picks 7 |
| a > 5 AND a != 6 | Tier 1 picks 6 | Domain: {7,8,...}, picks 7 |
| b = a + 1 with a > 5 | Can't solve b without a | Dependency: assign a=6, derive b=7 |
| JOIN a.id = b.fk + a.id > 50 | Independent solving | Shared domain, both get 51 |
| a IN (1,2,3) AND a > 2 | Picks 1 | Domain: {3}, picks 3 |
| UNIQUE avoidance + range | Picks value in range but conflicts | not_equals narrows domain |
| name = 'X' AND code = name | Can't derive code from name | Derived: code = name's value |

### Implementation Sequence

| Step | What | Where | Lines |
|------|------|-------|-------|
| 1 | Domain dataclass + is_empty() + pick() | solver/domain_solver.py | ~80 |
| 2 | CSPVariable + CSPConstraint dataclasses | solver/domain_solver.py | ~20 |
| 3 | build_csp() — extract variables + constraints from predicates | solver/domain_solver.py | ~80 |
| 4 | propagate() — AC-3 lite domain narrowing | solver/domain_solver.py | ~60 |
| 5 | assign() — topological sort + pick + backtrack | solver/domain_solver.py | ~50 |
| 6 | DomainSolver.solve() — orchestrate build → propagate → assign | solver/domain_solver.py | ~30 |
| 7 | Integrate into Solver.solve() (replace Tier 0 + Tier 1) | solver/unified.py | ~30 |
| 8 | Integrate into Resolver._build_row() | symbolic/speculate.py | ~20 |
| Total | | | ~370 |

### Design Decisions

1. No full backtracking search: SQL predicates are almost always acyclic dependency chains. A topological sort + single-pass assignment handles 99% of cases. If it fails, 
we fall through to Z3.

2. Domain representation is type-aware: INT domains use min/max bounds. TEXT domains use pattern matching. DATE domains use temporal bounds. The pick() method is type-
dispatched.

3. SharedKeys are just equality constraints: a.id = b.fk becomes a CSP constraint that unifies their domains. No special handling needed.

4. UNIQUE avoidance is just not_equals: Existing values in the Instance are added to the domain's exclusion set before solving.

5. The domain solver is stateless: It takes variables + constraints, returns assignments. No instance mutation, no side effects. Easy to test in isolation.

6. Graceful degradation: If the domain solver can't handle a constraint (e.g., non-linear arithmetic), it returns None and the unified solver falls through to Z3. No crash,
no incorrect results.

### Expected Impact

- **Correctness**: Multi-predicate queries that currently produce wrong values (and thus empty results) will get correct coordinated values.
- **Performance**: Domain solving is O(n×m) — much faster than Z3 for simple constraints. The 92% → ~95%+ improvement comes from fixing the multi-predicate failures.
- **Simplicity**: Replaces ~150 lines of ad-hoc _try_trivial + _try_heuristic with a principled ~300-line domain solver that handles all cases uniformly.

