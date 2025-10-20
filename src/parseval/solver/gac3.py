from typing import Dict, List, Set, Callable, Tuple, Any
from copy import deepcopy


class Variable:
    """Represents a CSP variable with a domain of possible values."""

    def __init__(self, name: str, domain: List[Any]):
        self.name = name
        self.domain = set(domain)

    def __repr__(self):
        return f"Variable({self.name}, domain={sorted(self.domain)})"


class Constraint:
    """Represents a constraint between variables."""

    def __init__(self, variables: List[str], predicate: Callable):
        self.variables = variables
        self.predicate = predicate

    def is_satisfied(self, assignment: Dict[str, Any]) -> bool:
        """Check if constraint is satisfied given an assignment."""
        # Only check if all variables in constraint are assigned
        if all(var in assignment for var in self.variables):
            values = [assignment[var] for var in self.variables]
            return self.predicate(*values)
        return True

    def __repr__(self):
        return f"Constraint({self.variables})"


class CSP:
    """Constraint Satisfaction Problem with GAC-3 algorithm."""

    def __init__(self):
        self.variables: Dict[str, Variable] = {}
        self.constraints: List[Constraint] = []

    def add_variable(self, name: str, domain: List[Any]):
        """Add a variable to the CSP."""
        self.variables[name] = Variable(name, domain)

    def add_constraint(self, variables: List[str], predicate: Callable):
        """Add a constraint to the CSP."""
        constraint = Constraint(variables, predicate)
        self.constraints.append(constraint)

    def get_constraints_for_variable(self, var_name: str) -> List[Constraint]:
        """Get all constraints involving a variable."""
        return [c for c in self.constraints if var_name in c.variables]

    def revise(self, var: str, constraint: Constraint, domains: Dict[str, Set]) -> bool:
        """
        Revise domain of var by removing values inconsistent with constraint.
        Returns True if domain was revised.
        """
        revised = False
        values_to_remove = set()

        for value in domains[var]:
            # Check if there exists a support for this value
            has_support = False

            # Try all combinations of values for other variables in constraint
            other_vars = [v for v in constraint.variables if v != var]

            if not other_vars:
                # Unary constraint
                assignment = {var: value}
                has_support = constraint.is_satisfied(assignment)
            else:
                # Check if there's at least one valid assignment
                has_support = self._find_support(
                    var, value, constraint, other_vars, domains
                )

            if not has_support:
                values_to_remove.add(value)
                revised = True

        domains[var] -= values_to_remove
        return revised

    def _find_support(
        self,
        var: str,
        value: Any,
        constraint: Constraint,
        other_vars: List[str],
        domains: Dict[str, Set],
    ) -> bool:
        """Find if there's a support for var=value in the constraint."""

        # Generate all combinations of values for other variables
        def generate_assignments(vars_list, index=0, current=None):
            if current is None:
                current = {}

            if index == len(vars_list):
                assignment = current.copy()
                assignment[var] = value
                if constraint.is_satisfied(assignment):
                    return True
                return False

            current_var = vars_list[index]
            for val in domains[current_var]:
                current[current_var] = val
                if generate_assignments(vars_list, index + 1, current):
                    return True
                del current[current_var]

            return False

        return generate_assignments(other_vars)

    def gac3(self, domains: Dict[str, Set] = None) -> Dict[str, Set]:
        """
        Apply GAC-3 algorithm to enforce generalized arc consistency.
        Returns the reduced domains or None if inconsistent.
        """
        if domains is None:
            domains = {name: var.domain.copy() for name, var in self.variables.items()}
        else:
            domains = {name: domain.copy() for name, domain in domains.items()}

        # Initialize queue with all (variable, constraint) pairs
        queue = []
        for var_name in self.variables:
            for constraint in self.get_constraints_for_variable(var_name):
                queue.append((var_name, constraint))

        while queue:
            var_name, constraint = queue.pop(0)

            if self.revise(var_name, constraint, domains):
                if not domains[var_name]:
                    return None  # Domain is empty, no solution

                # Add all constraints of neighboring variables to queue
                for other_constraint in self.get_constraints_for_variable(var_name):
                    for other_var in other_constraint.variables:
                        if other_var != var_name:
                            queue.append((other_var, other_constraint))

        return domains

    def solve(self, use_gac=True) -> List[Dict[str, Any]]:
        """
        Solve the CSP using backtracking search with optional GAC.
        Returns all solutions.
        """
        # Apply initial GAC
        if use_gac:
            domains = self.gac3()
            if domains is None:
                return []
        else:
            domains = {name: var.domain.copy() for name, var in self.variables.items()}

        solutions = []
        self._backtrack({}, domains, solutions, use_gac)
        return solutions

    def _backtrack(
        self,
        assignment: Dict[str, Any],
        domains: Dict[str, Set],
        solutions: List[Dict[str, Any]],
        use_gac: bool,
    ):
        """Recursive backtracking search."""
        # Check if assignment is complete
        if len(assignment) == len(self.variables):
            solutions.append(assignment.copy())
            return

        # Select unassigned variable (using MRV heuristic)
        unassigned = [v for v in self.variables if v not in assignment]
        var = min(unassigned, key=lambda v: len(domains[v]))

        # Try each value in domain
        for value in sorted(domains[var]):
            # Check if value is consistent with current assignment
            test_assignment = assignment.copy()
            test_assignment[var] = value

            if self._is_consistent(test_assignment):
                assignment[var] = value

                # Apply GAC if enabled
                if use_gac:
                    new_domains = {k: v.copy() for k, v in domains.items()}
                    new_domains[var] = {value}
                    new_domains = self.gac3(new_domains)

                    if new_domains is not None:
                        self._backtrack(assignment, new_domains, solutions, use_gac)
                else:
                    self._backtrack(assignment, domains, solutions, use_gac)

                del assignment[var]

    def _is_consistent(self, assignment: Dict[str, Any]) -> bool:
        """Check if partial assignment is consistent with all constraints."""
        for constraint in self.constraints:
            if not constraint.is_satisfied(assignment):
                return False
        return True


# Example usage
if __name__ == "__main__":
    # Example 1: a + b + c > 100
    print("Example 1: a + b + c > 100")
    csp1 = CSP()
    csp1.add_variable("a", list(range(0, 60)))
    csp1.add_variable("b", list(range(0, 60)))
    csp1.add_variable("c", list(range(0, 60)))
    csp1.add_constraint(["a", "b", "c"], lambda a, b, c: a + b + c > 100)

    solutions1 = csp1.solve()
    print(f"Found {len(solutions1)} solutions")
    print("First 5 solutions:", solutions1[:5])
    print()

    # Example 2: All different constraint
    print("Example 2: All different (a, b, c in [1,2,3])")
    csp2 = CSP()
    csp2.add_variable("a", [1, 2, 3])
    csp2.add_variable("b", [1, 2, 3])
    csp2.add_variable("c", [1, 2, 3])
    csp2.add_constraint(["a", "b"], lambda a, b: a == b)
    csp2.add_constraint(["b", "c"], lambda b, c: b == c)
    csp2.add_constraint(["a", "c"], lambda a, c: a != c)

    solutions2 = csp2.solve()
    print(f"Found {len(solutions2)} solutions")
    print("All solutions:", solutions2)
    print()

    # Example 3: Complex constraint
    print("Example 3: x^2 + y^2 < 100, x > y")
    csp3 = CSP()
    csp3.add_variable("x", list(range(0, 10)))
    csp3.add_variable("y", list(range(0, 10)))
    csp3.add_constraint(["x", "y"], lambda x, y: x**2 + y**2 < 100)
    csp3.add_constraint(["x", "y"], lambda x, y: x > y)

    solutions3 = csp3.solve()
    print(f"Found {len(solutions3)} solutions")
    print("All solutions:", solutions3[:10])
