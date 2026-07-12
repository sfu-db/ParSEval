"""Partition a Problem into connected components by SolverVar.

Connectivity comes from ``SolverVar`` leaves embedded in constraint ASTs.
Top-level ``And`` / ``Paren`` are flattened first so independent conjuncts
(e.g. ``a > 5 AND b > 5``) stay in separate components. Variables are only
unioned when they co-occur in the same atomic predicate, or via an explicit
equality edge.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from .types import Problem, SolverVar, collect_problem_variables


def expression_variables(expr: exp.Expression) -> Set[SolverVar]:
    return set(expr.find_all(SolverVar))


def flatten_conjuncts(expr: exp.Expression) -> List[exp.Expression]:
    """Flatten top-level And/Paren into a list of atomic conjuncts."""
    if isinstance(expr, exp.Paren):
        return flatten_conjuncts(expr.this)
    if isinstance(expr, exp.And):
        return flatten_conjuncts(expr.this) + flatten_conjuncts(expr.expression)
    return [expr]


def partition(problem: Problem) -> List[Problem]:
    """Split *problem* into connected components over shared SolverVars."""
    if not problem.constraints and not problem.equalities:
        return []

    # Flatten so And does not falsely connect independent atoms.
    atoms: List[exp.Expression] = []
    for expr in problem.constraints:
        atoms.extend(flatten_conjuncts(expr))

    parent: Dict[SolverVar, SolverVar] = {}

    def add(variable: SolverVar) -> None:
        parent.setdefault(variable, variable)

    def find(variable: SolverVar) -> SolverVar:
        add(variable)
        while parent[variable] != variable:
            parent[variable] = parent[parent[variable]]
            variable = parent[variable]
        return variable

    def union(left: SolverVar, right: SolverVar) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[left_root] = right_root

    atom_vars = [expression_variables(atom) for atom in atoms]

    # Connect only vars that co-occur in the same atomic predicate.
    for variables in atom_vars:
        for variable in variables:
            add(variable)
        if len(variables) > 1:
            first = next(iter(variables))
            for variable in variables:
                union(first, variable)

    # Explicit equality edges (join/FK facts not always present as EQ atoms).
    for left_var, right_var in problem.equalities:
        union(left_var, right_var)

    for variable in collect_problem_variables(problem):
        add(variable)

    grouped_atoms: Dict[object, List[exp.Expression]] = {}
    grouped_eqs: Dict[object, List[Tuple[SolverVar, SolverVar]]] = {}
    grouped_vars: Dict[object, Set[SolverVar]] = {}

    for index, atom in enumerate(atoms):
        variables = atom_vars[index]
        key: object = find(next(iter(variables))) if variables else ("atom", index)
        grouped_atoms.setdefault(key, []).append(atom)
        grouped_vars.setdefault(key, set()).update(variables)

    for left_var, right_var in problem.equalities:
        key = find(left_var)
        grouped_eqs.setdefault(key, []).append((left_var, right_var))
        grouped_vars.setdefault(key, set()).update((left_var, right_var))

    for variable in collect_problem_variables(problem):
        key = find(variable)
        grouped_vars.setdefault(key, set()).add(variable)

    keys = set(grouped_atoms) | set(grouped_eqs) | {
        find(v) for v in collect_problem_variables(problem)
    }
    components: List[Problem] = []
    for key in keys:
        component_vars = grouped_vars.get(key, set())
        components.append(
            Problem(
                constraints=grouped_atoms.get(key, []),
                equalities=grouped_eqs.get(key, []),
                variables=component_vars,
            )
        )
    return components


__all__ = ["expression_variables", "flatten_conjuncts", "partition"]
