What’s duplicated today (functional overlap across domain/, symbolic/speculate.py, solver/)

  1. Predicate → column-constraints lowering
      - src/parseval/symbolic/speculate.py:
          - _iter_atoms, _atom_to_constraint, _extract_predicates, _extract_negated_predicates, _negate_constraint
      - src/parseval/solver/unified.py:
          - _extract_expr_constraints, _col_lit (+ legacy Tier 0/1 helpers still present)
      - These both implement partial SQL predicate parsing (EQ/GT/GTE/LT/LTE/BETWEEN/LIKE/IS NULL, etc.) and “pick a satisfying value” heuristics.
  2. “Which table/column does this Column refer to?” resolution
      - src/parseval/symbolic/constraints.py: ConstraintGenerator._resolve_table, _resolve_table_name
      - src/parseval/solver/unified.py: _resolve_table + Solver._resolve
      - src/parseval/symbolic/speculate.py: Propagator._resolve_table, _match_column
      - Same job, different rules/edge cases, guaranteed drift over time.
  3. JOIN key coordination (“shared keys”)
      - src/parseval/symbolic/speculate.py: TableRequirement.shared_keys + Resolver._resolve_shared_keys
      - src/parseval/solver/unified.py: join_equalities -> shared_keys -> DomainSolver plus _apply_join_equalities
      - Both create “equalize these columns” constraints and invent concrete key values.
  4. FK realization / parent seeding
      - src/parseval/symbolic/engine.py: _fill_fk_values
      - src/parseval/solver/unified.py: _apply_fk_constraints
      - src/parseval/domain/builder.py: FK validation + composite-FK bindings + parent existence rules
      - Same responsibilities split across 3 layers (solver, symbolic, domain), with subtly different policies.
  5. Value picking defaults / type-family logic
      - src/parseval/solver/unified.py: _default_for_family, _different_value, _column_profile (ad hoc defaults)
      - src/parseval/symbolic/speculate.py: _resolve_shared_keys (string-based INT/TEXT checks)
      - src/parseval/solver/value_space.py: ValueSpace.pick() (its own defaults)
      - src/parseval/domain/providers/*: canonical family-aware generators
      - Multiple “default value” sources means inconsistent behavior and duplicated tuning.

  ———

  ## Refactor direction (unified interfaces) — a concrete plan, no code changes yet

  ### Phase 0: Write down the target boundaries (keep this crisp)

  Adopt the boundary already documented in .agents/learnings/2026-05-10-symbolic-solver-boundary.md:

  - symbolic/ owns: coverage model, target selection, evaluation loop.
  - solver/ owns: turning a “goal” into concrete rows (SMT, CSP/value-space, speculative/top-down, heuristics).
  - domain/ owns: schema/type system, coercion, domain plans, provider-based generation, constraint validation.

  Success criterion: symbolic never “generates values” or “fixes FKs” directly; it only asks solver to realize constraints on an instance.

  ### Phase 1: Inventory APIs + identify callers (so we don’t break flows)

  1. List current entrypoints used by symbolic:
      - SymbolicEngine._speculate* uses symbolic.speculate.build_spec/resolve_spec
      - SymbolicEngine uses ConstraintGenerator → Solver.solve(SolverConstraint)
  2. List what solver expects today (symbolic.constraints.SolverConstraint fields).
  3. List what speculative currently returns (per-table rows; sometimes flattened first-row only).

  Output: a short “call graph” doc showing the two parallel paths:

  - speculative witness path (top-down)
  - targeted constraint path (ConstraintGenerator + Solver)

  ### Phase 2: Define one solver-facing request/response contract

  Introduce (design-only for now) a single solver API that both “speculate” and “targeted atom outcomes” can use:

  - SolveRequest
      - target_tables: tuple[str, ...]
      - constraints: NormalizedConstraints
          - predicates: list[PredicateExpr] (or already-lowered column predicates)
          - equalities: list[ColumnEq] (join/shared key)
          - not_null: list[ColumnRef]
          - avoid_values: dict[ColumnRef, set[Any]]
          - foreign_keys: list[ForeignKeyEdge]
      - goal: SolveGoal
          - WitnessNonEmpty() (speculative “get at least one output row”)
          - MakeAtomOutcome(atom_expr, outcome) (current targeted mode)
          - (optional later) MakeJoinMatch/NoMatch, GroupMulti, etc.
  - SolveResponse
      - sat: bool
      - rows: dict[str, list[dict[str, Any]]] (allow multi-row per table; single-row is just length 1)
      - reason: str

  Key rule: solver returns rows; symbolic applies them via Instance.create_row. No extra FK fill in symbolic.

  ### Phase 3: Centralize “lowering” once (remove predicate parsing duplication)

  Create a single conceptual component in solver (design target: parseval.solver.lowering):

  - ExprLowerer.lower(expr, context) -> list[ColumnPredicate] + residual_exprs
      - Handles the subset you already duplicate:
          - comparisons, BETWEEN, LIKE, IN-lists, IS NULL / IS NOT NULL
          - basic AND/Paren decomposition
      - Anything not handled becomes “residual” and must be satisfied by SMT (or ignored explicitly with reason).

  Then:

  - symbolic.speculate.Propagator stops doing _atom_to_constraint for value picking; it instead produces expressions (or a normalized constraint form) and hands them to solver.
  - solver.unified.Solver._extract_expr_constraints becomes a thin wrapper around the same lowerer (or disappears).

  ### Phase 4: Centralize table/column resolution utilities

  Define one resolution policy (design target: parseval.solver.resolution or parseval.utils.sql_resolve):

  - resolve_table_for_column(exp.Column, candidate_tables, instance, alias_map?) -> table
  - match_instance_column(table, column_name) -> canonical_column_name

  Then replace:

  - ConstraintGenerator._resolve_table
  - solver.unified._resolve_table
  - Propagator._resolve_table/_match_column

  This eliminates drift and makes alias behavior consistent.

  ### Phase 5: Choose one “row realization pipeline” inside solver

  Right now you have three realization styles:

  - speculative top-down (symbolic/speculate.py)
  - CSP/value-space (solver/value_space.py DomainSolver)
  - SMT (solver/smt.py)

  Unify them behind strategies, but keep one public API:

  - Solver.solve(request) chooses a strategy:
      1. CspStrategy (DomainSolver/value-space)
      2. SmtStrategy
      3. SpeculativeStrategy (moved from symbolic into solver, rewritten to output SolveResponse.rows)

  Important: speculative should become “just another solver strategy”, not a parallel subsystem in symbolic/.

  ### Phase 6: Make FK + uniqueness enforcement single-sourced

  Pick exactly one layer that “owns” FK completion policy. Recommended:

  - solver/ owns “find/seed parent rows” policy (because it’s about realization orchestration).
  - domain/ owns “is this row valid?” checking/coercion semantics.

  Concretely (design):

  - solver uses domain services to:
      - coerce candidate values (domain.coercion.coerce_value)
      - compare for equivalence (domain.coercion.values_equivalent)
      - generate typed values (provider registry / TypeService)
  - symbolic never calls _fill_fk_values.
  - speculative shared-key generation should use TypeService/TypeFamily rather than "INT" in type_str style checks.

  ### Phase 7: De-dup default value generation

  Make defaults come from domain providers (or a single solver helper built on them), and delete/avoid:

  - solver.unified._default_for_family / ad hoc “value” strings
  - speculative hardcoded key_1 / numeric increments where TypeFamily says otherwise

  Design target: domain exposes a small “value factory”:

  - DomainValueFactory.sample(family, profile, constraints, rng)

  Then both CSP ValueSpace.pick() and speculative shared-key resolution can call the same factory.