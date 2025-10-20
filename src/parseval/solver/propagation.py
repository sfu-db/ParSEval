from typing import Set, Dict, List, Callable, Any, Optional, Tuple, Union
from collections import deque
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import random


class DomainType(Enum):
    """Types of domains that can be auto-generated."""

    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    EMAIL = "email"
    DATE = "date"
    BOOLEAN = "boolean"
    CATEGORY = "category"


@dataclass
class DomainSpec:
    """Specification for auto-generating a domain."""

    type: DomainType
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    length: Optional[int] = None
    pattern: Optional[str] = None
    categories: Optional[List[str]] = None
    sample_size: int = 20  # Number of values to generate for the domain

    def generate_domain(self) -> Set[Any]:
        """Generate a domain based on the specification."""
        if self.type == DomainType.INTEGER:
            return self._generate_integer_domain()
        elif self.type == DomainType.FLOAT:
            return self._generate_float_domain()
        elif self.type == DomainType.STRING:
            return self._generate_string_domain()
        elif self.type == DomainType.EMAIL:
            return self._generate_email_domain()
        elif self.type == DomainType.DATE:
            return self._generate_date_domain()
        elif self.type == DomainType.BOOLEAN:
            return {True, False}
        elif self.type == DomainType.CATEGORY:
            return set(self.categories) if self.categories else set()
        else:
            raise ValueError(f"Unknown domain type: {self.type}")

    def _generate_integer_domain(self) -> Set[int]:
        min_val = self.min_value if self.min_value is not None else 0
        max_val = self.max_value if self.max_value is not None else 100

        # Generate a reasonable sample
        if max_val - min_val <= self.sample_size:
            return set(range(min_val, max_val + 1))
        else:
            # Sample uniformly
            step = (max_val - min_val) // self.sample_size
            return set(range(min_val, max_val + 1, max(1, step)))

    def _generate_float_domain(self) -> Set[float]:
        min_val = self.min_value if self.min_value is not None else 0.0
        max_val = self.max_value if self.max_value is not None else 100.0

        step = (max_val - min_val) / self.sample_size
        return {round(min_val + i * step, 2) for i in range(self.sample_size + 1)}

    def _generate_string_domain(self) -> Set[str]:
        length = self.length if self.length is not None else 5

        if self.pattern:
            # Simple pattern matching (e.g., "PREFIX_*")
            if "*" in self.pattern:
                prefix = self.pattern.split("*")[0]
                return {f"{prefix}{i}" for i in range(self.sample_size)}
            return {self.pattern}

        # Generate random strings
        import string

        chars = string.ascii_lowercase
        return {
            "".join(random.choices(chars, k=length)) for _ in range(self.sample_size)
        }

    def _generate_email_domain(self) -> Set[str]:
        names = [
            "john",
            "jane",
            "bob",
            "alice",
            "charlie",
            "diana",
            "eve",
            "frank",
            "grace",
            "henry",
        ]
        domains = ["gmail.com", "yahoo.com", "company.com", "test.org"]

        emails = set()
        for name in names[: self.sample_size // 2]:
            for domain in domains[:2]:
                emails.add(f"{name}@{domain}")
                if len(emails) >= self.sample_size:
                    return emails
        return emails

    def _generate_date_domain(self) -> Set[str]:
        # Generate dates in YYYY-MM-DD format
        years = [2024, 2025]
        months = list(range(1, 13))
        days = list(range(1, 29))  # Simplified to avoid month validation

        dates = set()
        for _ in range(self.sample_size):
            y = random.choice(years)
            m = random.choice(months)
            d = random.choice(days)
            dates.add(f"{y}-{m:02d}-{d:02d}")
        return dates


class Variable:
    """Represents a variable with a domain of possible values."""

    def __init__(self, name: str, domain: Set[Any]):
        self.name = name
        self.domain = domain.copy()
        self.initial_domain = domain.copy()

    def __repr__(self):
        return f"Variable({self.name}, domain_size={len(self.domain)})"

    def reset(self):
        """Reset domain to initial state."""
        self.domain = self.initial_domain.copy()


class Constraint(ABC):
    """Abstract base class for constraints."""

    def __init__(self, variables: List[Variable]):
        self.variables = variables

    @abstractmethod
    def is_satisfied(self, assignment: Dict[Variable, Any]) -> bool:
        """Check if the constraint is satisfied given an assignment."""
        pass

    @abstractmethod
    def propagate(self) -> bool:
        """
        Propagate the constraint to reduce variable domains.
        Returns True if any domain was modified, False otherwise.
        """
        pass

    def __repr__(self):
        var_names = [v.name for v in self.variables]
        return f"{self.__class__.__name__}({', '.join(var_names)})"


class AllDifferentConstraint(Constraint):
    """Constraint that ensures all variables have different values."""

    def is_satisfied(self, assignment: Dict[Variable, Any]) -> bool:
        values = [assignment[v] for v in self.variables if v in assignment]
        return len(values) == len(set(values))

    def propagate(self) -> bool:
        modified = False

        # Find variables with singleton domains
        assigned = {}
        for var in self.variables:
            if len(var.domain) == 1:
                assigned[var] = next(iter(var.domain))

        # Remove assigned values from other variables' domains
        for var in self.variables:
            if var not in assigned and len(var.domain) > 1:
                original_size = len(var.domain)
                var.domain -= set(assigned.values())
                if len(var.domain) < original_size:
                    modified = True

                if len(var.domain) == 0:
                    raise ValueError(f"Domain of {var.name} became empty!")

        return modified


class BinaryConstraint(Constraint):
    """Binary constraint between two variables using a custom predicate."""

    def __init__(
        self, var1: Variable, var2: Variable, predicate: Callable[[Any, Any], bool]
    ):
        super().__init__([var1, var2])
        self.var1 = var1
        self.var2 = var2
        self.predicate = predicate

    def is_satisfied(self, assignment: Dict[Variable, Any]) -> bool:
        if self.var1 not in assignment or self.var2 not in assignment:
            return True
        return self.predicate(assignment[self.var1], assignment[self.var2])

    def propagate(self) -> bool:
        modified = False

        # Filter var1's domain
        valid_values_1 = set()
        for val1 in self.var1.domain:
            if any(self.predicate(val1, val2) for val2 in self.var2.domain):
                valid_values_1.add(val1)

        if len(valid_values_1) < len(self.var1.domain):
            self.var1.domain = valid_values_1
            modified = True
            if len(self.var1.domain) == 0:
                raise ValueError(f"Domain of {self.var1.name} became empty!")

        # Filter var2's domain
        valid_values_2 = set()
        for val2 in self.var2.domain:
            if any(self.predicate(val1, val2) for val1 in self.var1.domain):
                valid_values_2.add(val2)

        if len(valid_values_2) < len(self.var2.domain):
            self.var2.domain = valid_values_2
            modified = True
            if len(self.var2.domain) == 0:
                raise ValueError(f"Domain of {self.var2.name} became empty!")

        return modified


class UnaryConstraint(Constraint):
    """Unary constraint on a single variable."""

    def __init__(self, variable: Variable, predicate: Callable[[Any], bool]):
        super().__init__([variable])
        self.variable = variable
        self.predicate = predicate

    def is_satisfied(self, assignment: Dict[Variable, Any]) -> bool:
        if self.variable not in assignment:
            return True
        return self.predicate(assignment[self.variable])

    def propagate(self) -> bool:
        original_size = len(self.variable.domain)
        self.variable.domain = {
            val for val in self.variable.domain if self.predicate(val)
        }

        if len(self.variable.domain) == 0:
            raise ValueError(f"Domain of {self.variable.name} became empty!")

        return len(self.variable.domain) < original_size


class RowConstraint(Constraint):
    """Constraint that applies to all values in a row (tuple constraint)."""

    def __init__(
        self, variables: List[Variable], predicate: Callable[[Tuple[Any, ...]], bool]
    ):
        super().__init__(variables)
        self.predicate = predicate

    def is_satisfied(self, assignment: Dict[Variable, Any]) -> bool:
        if not all(v in assignment for v in self.variables):
            return True
        values = tuple(assignment[v] for v in self.variables)
        return self.predicate(values)

    def propagate(self) -> bool:
        """Use arc consistency for n-ary constraints."""
        modified = False

        for i, var in enumerate(self.variables):
            valid_values = set()

            for value in var.domain:
                # Check if there exists a valid assignment for other variables
                if self._has_support(i, value):
                    valid_values.add(value)

            if len(valid_values) < len(var.domain):
                var.domain = valid_values
                modified = True
                if len(var.domain) == 0:
                    raise ValueError(f"Domain of {var.name} became empty!")

        return modified

    def _has_support(self, var_index: int, value: Any) -> bool:
        """Check if value has support in the constraint."""

        def check_recursive(idx: int, current: List[Any]) -> bool:
            if idx == len(self.variables):
                return self.predicate(tuple(current))

            if idx == var_index:
                return check_recursive(idx + 1, current + [value])

            for val in self.variables[idx].domain:
                if check_recursive(idx + 1, current + [val]):
                    return True
            return False

        return check_recursive(0, [])


class ConstraintPropagationSolver:
    """Main solver using constraint propagation (AC-3 algorithm)."""

    def __init__(self):
        self.variables: List[Variable] = []
        self.constraints: List[Constraint] = []

    def add_variable(self, variable: Variable):
        """Add a variable to the problem."""
        self.variables.append(variable)

    def add_constraint(self, constraint: Constraint):
        """Add a constraint to the problem."""
        self.constraints.append(constraint)

    def propagate(self) -> bool:
        """
        Apply constraint propagation until fixpoint.
        Returns True if successful, False if inconsistency detected.
        """
        queue = deque(self.constraints)

        try:
            while queue:
                constraint = queue.popleft()
                if constraint.propagate():
                    # If a domain was modified, re-add related constraints
                    for other_constraint in self.constraints:
                        if other_constraint != constraint and any(
                            v in constraint.variables
                            for v in other_constraint.variables
                        ):
                            if other_constraint not in queue:
                                queue.append(other_constraint)
            return True
        except ValueError as e:
            print(f"Inconsistency detected: {e}")
            return False

    def solve(self) -> Optional[Dict[Variable, Any]]:
        """
        Solve the constraint satisfaction problem.
        Returns a solution if found, None otherwise.
        """
        # First, apply constraint propagation
        if not self.propagate():
            return None

        # Check if we have a complete solution
        if all(len(var.domain) == 1 for var in self.variables):
            return {var: next(iter(var.domain)) for var in self.variables}

        # Otherwise, use backtracking search
        return self._backtrack({})

    def _backtrack(
        self, assignment: Dict[Variable, Any]
    ) -> Optional[Dict[Variable, Any]]:
        """Backtracking search with constraint propagation."""
        if len(assignment) == len(self.variables):
            return assignment

        # Select unassigned variable with smallest domain (MRV heuristic)
        unassigned = [v for v in self.variables if v not in assignment]
        var = min(unassigned, key=lambda v: len(v.domain))

        for value in list(var.domain):
            if self._is_consistent(var, value, assignment):
                assignment[var] = value

                # Save current state
                saved_domains = {v: v.domain.copy() for v in self.variables}

                # Forward checking: reduce domains
                var.domain = {value}
                if self.propagate():
                    result = self._backtrack(assignment)
                    if result is not None:
                        return result

                # Restore domains
                for v in self.variables:
                    v.domain = saved_domains[v]

                del assignment[var]

        return None

    def _is_consistent(
        self, var: Variable, value: Any, assignment: Dict[Variable, Any]
    ) -> bool:
        """Check if assigning value to var is consistent with constraints."""
        assignment[var] = value
        consistent = all(c.is_satisfied(assignment) for c in self.constraints)
        del assignment[var]
        return consistent

    def reset(self):
        """Reset all variables to their initial domains."""
        for var in self.variables:
            var.reset()


class TableGenerator:
    """Generate table data satisfying row and column constraints with auto-generated domains."""

    def __init__(
        self, num_rows: int, column_specs: Dict[str, DomainSpec], verbose: bool = False
    ):
        self.num_rows = num_rows
        self.column_specs = column_specs
        self.column_names = list(column_specs.keys())
        self.verbose = verbose

        # Auto-generate domains from specs
        self.column_domains = {
            col: spec.generate_domain() for col, spec in column_specs.items()
        }

        if self.verbose:
            print("Initial domains generated:")
            for col, domain in self.column_domains.items():
                print(f"  {col}: {len(domain)} values (sample: {list(domain)[:5]})")

        self.row_constraints: List[Callable[[Tuple[Any, ...]], bool]] = []
        self.column_constraints: Dict[str, List[Callable[[Any], bool]]] = {
            col: [] for col in self.column_names
        }
        self.inter_row_constraints: List[Tuple[List[str], Callable]] = []

        # Apply domain-based constraints automatically
        self._apply_domain_constraints()

    def _apply_domain_constraints(self):
        """Apply constraints based on domain specifications."""
        for col, spec in self.column_specs.items():
            if spec.type == DomainType.INTEGER:
                if spec.min_value is not None:
                    self.add_column_constraint(col, lambda x, m=spec.min_value: x >= m)
                if spec.max_value is not None:
                    self.add_column_constraint(col, lambda x, m=spec.max_value: x <= m)

            elif spec.type == DomainType.FLOAT:
                if spec.min_value is not None:
                    self.add_column_constraint(col, lambda x, m=spec.min_value: x >= m)
                if spec.max_value is not None:
                    self.add_column_constraint(col, lambda x, m=spec.max_value: x <= m)

    def add_row_constraint(self, constraint: Callable[[Tuple[Any, ...]], bool]):
        """Add a constraint that applies to each row."""
        self.row_constraints.append(constraint)

    def add_column_constraint(self, column: str, constraint: Callable[[Any], bool]):
        """Add a constraint that applies to a specific column."""
        if column in self.column_constraints:
            self.column_constraints[column].append(constraint)

    def add_inter_row_constraint(
        self, columns: List[str], constraint: Callable[[Dict[str, List[Any]]], bool]
    ):
        """Add a constraint across multiple rows for specific columns."""
        self.inter_row_constraints.append((columns, constraint))

    def expand_domain(self, column: str, additional_samples: int = 10):
        """Expand a column's domain with more generated values."""
        spec = self.column_specs[column]
        old_size = spec.sample_size
        spec.sample_size += additional_samples
        new_domain = spec.generate_domain()
        self.column_domains[column] = new_domain
        spec.sample_size = old_size  # Reset for future use

        if self.verbose:
            print(f"Expanded {column} domain to {len(new_domain)} values")

    def validate_constraints(self) -> bool:
        """Check if constraints are satisfiable by testing sample combinations."""
        if self.verbose:
            print("\nValidating constraint satisfiability...")

        # Test row constraints with sample values
        row_satisfiable = False
        test_attempts = 100

        for _ in range(test_attempts):
            test_row = tuple(
                random.choice(list(self.column_domains[col]))
                for col in self.column_names
            )
            if all(constraint(test_row) for constraint in self.row_constraints):
                row_satisfiable = True
                if self.verbose:
                    print(f"  ✓ Found satisfiable row: {test_row}")
                break

        if not row_satisfiable and self.row_constraints:
            if self.verbose:
                print(
                    f"  ✗ No satisfiable row found in {test_attempts} random attempts"
                )
                print(
                    "  Suggestion: Constraints may be too restrictive or domains too small"
                )
            return False

        return True

    def generate(self, max_attempts: int = 3) -> Optional[List[Dict[str, Any]]]:
        """Generate a valid table satisfying all constraints."""

        # First validate that constraints are satisfiable
        if not self.validate_constraints():
            print("⚠ Warning: Constraints may not be satisfiable with current domains")

        for attempt in range(max_attempts):
            if self.verbose:
                print(f"\n--- Attempt {attempt + 1}/{max_attempts} ---")

            try:
                result = self._attempt_generation()
                if result is not None:
                    if self.verbose:
                        print(
                            f"✓ Successfully generated table on attempt {attempt + 1}"
                        )
                    return result

                # If failed, expand domains and try again
                if self.verbose:
                    print(f"✗ Attempt {attempt + 1} failed, expanding domains...")
                for col in self.column_names:
                    self.expand_domain(col, 20)

            except Exception as e:
                if self.verbose:
                    print(f"✗ Attempt {attempt + 1} error: {e}")
                continue

        print(f"Failed to generate table after {max_attempts} attempts")
        return None

    def _attempt_generation(self) -> Optional[List[Dict[str, Any]]]:
        """Single attempt at generating the table."""
        solver = ConstraintPropagationSolver()

        # Create variables for each cell in the table
        cell_vars = {}
        for row in range(self.num_rows):
            for col in self.column_names:
                var_name = f"R{row}_{col}"
                domain = self.column_domains[col].copy()
                var = Variable(var_name, domain)
                cell_vars[(row, col)] = var
                solver.add_variable(var)

        # Add column constraints
        for col in self.column_names:
            for row in range(self.num_rows):
                var = cell_vars[(row, col)]
                for constraint in self.column_constraints[col]:
                    solver.add_constraint(UnaryConstraint(var, constraint))

        # Add row constraints
        for row in range(self.num_rows):
            row_vars = [cell_vars[(row, col)] for col in self.column_names]
            for constraint in self.row_constraints:
                solver.add_constraint(RowConstraint(row_vars, constraint))

        # Add inter-row constraints
        for columns, constraint in self.inter_row_constraints:
            vars_list = []
            for row in range(self.num_rows):
                for col in columns:
                    vars_list.append(cell_vars[(row, col)])

            def make_inter_row_predicate(cols, constr):
                def predicate(values):
                    col_values = {col: [] for col in cols}
                    idx = 0
                    for row in range(self.num_rows):
                        for col in cols:
                            col_values[col].append(values[idx])
                            idx += 1
                    return constr(col_values)

                return predicate

            solver.add_constraint(
                RowConstraint(vars_list, make_inter_row_predicate(columns, constraint))
            )

        # Solve the CSP
        solution = solver.solve()

        if solution is None:
            return None

        # Convert solution to table format
        table = []
        for row in range(self.num_rows):
            row_data = {}
            for col in self.column_names:
                row_data[col] = solution[cell_vars[(row, col)]]
            table.append(row_data)

        return table


# Example usage
if __name__ == "__main__":
    print("=== Example 1: Employee Schedule with Complex Constraint ===")

    generator = TableGenerator(
        num_rows=3,
        column_specs={
            "employee_id": DomainSpec(
                type=DomainType.INTEGER,
                min_value=400,
                max_value=1600,
                sample_size=150,  # Larger sample for complex constraint
            ),
            "email": DomainSpec(type=DomainType.EMAIL, sample_size=10),
            "hours": DomainSpec(
                type=DomainType.INTEGER,
                min_value=20,
                max_value=35,  # Adjusted for constraint
                sample_size=20,
            ),
            "is_remote": DomainSpec(type=DomainType.BOOLEAN),
        },
        verbose=True,  # Enable detailed output
    )

    # All employee IDs must be unique
    generator.add_inter_row_constraint(
        ["employee_id"],
        lambda cols: len(cols["employee_id"]) == len(set(cols["employee_id"])),
    )

    # All emails must be unique
    generator.add_inter_row_constraint(
        ["email"], lambda cols: len(cols["email"]) == len(set(cols["email"]))
    )

    # Complex row constraint: remote workers constraint + sum equation
    # employee_id + hours = 1500 AND hours <= 35 AND not is_remote
    generator.add_row_constraint(
        lambda row: (
            not row[3]
            and row[2] <= 35
            and row[0] + row[2] == 820
            # and row[0] + row[2] <= 600
        )
        # or (row[3] and row[2] <= 30)  # Alternative: remote workers with fewer hours
    )

    table = generator.generate(max_attempts=5)

    if table:
        print("\n" + "=" * 60)
        print("Generated Employee Schedule:")
        print(f"{'ID':<8} {'Email':<25} {'Hours':<8} {'Remote':<8} {'ID+Hours':<10}")
        print("-" * 60)
        for row in table:
            sum_val = row["employee_id"] + row["hours"]
            print(
                f"{row['employee_id']:<8} {row['email']:<25} "
                f"{row['hours']:<8} {row['is_remote']:<8} {sum_val:<10}"
            )
    else:
        print("\n⚠ No valid schedule found!")
        print("\nSuggestions:")
        print("1. Relax the constraint (e.g., use a range instead of exact sum)")
        print("2. Increase domain sizes (sample_size parameter)")
        print("3. Reduce the number of rows")
        print("4. Check if constraint is mathematically satisfiable")

    print("\n" + "=" * 60)
    print("=== Example 2: Product Pricing (Simpler Constraints) ===")

    generator2 = TableGenerator(
        num_rows=4,
        column_specs={
            "product": DomainSpec(
                type=DomainType.STRING, pattern="PROD_*", sample_size=10
            ),
            "category": DomainSpec(
                type=DomainType.CATEGORY,
                categories=["Electronics", "Furniture", "Clothing"],
            ),
            "price": DomainSpec(
                type=DomainType.FLOAT, min_value=10.0, max_value=500.0, sample_size=20
            ),
            "in_stock": DomainSpec(type=DomainType.BOOLEAN),
        },
        verbose=False,
    )

    # All products must be unique
    generator2.add_inter_row_constraint(
        ["product"], lambda cols: len(cols["product"]) == len(set(cols["product"]))
    )

    # Electronics must be more expensive
    generator2.add_row_constraint(
        lambda row: row[1] != "Electronics" or row[2] >= 100.0
    )

    # At least one product must be in stock
    generator2.add_inter_row_constraint(
        ["in_stock"], lambda cols: any(cols["in_stock"])
    )

    table2 = generator2.generate()

    if table2:
        print("\nGenerated Product Catalog:")
        print(f"{'Product':<12} {'Category':<15} {'Price':<10} {'In Stock':<10}")
        print("-" * 50)
        for row in table2:
            print(
                f"{row['product']:<12} {row['category']:<15} "
                f"${row['price']:<9.2f} {row['in_stock']:<10}"
            )
    else:
        print("No valid catalog found!")
