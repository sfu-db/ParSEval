"""
Hybrid SQL Symbolic Executor combining Z3 SMT Solver with CSP Framework
Uses Z3 for constraint solving and CSP for realistic data generation
"""

from typing import Set, Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import re

# Note: Install z3-solver via: pip install z3-solver
try:
    from z3 import *

    Z3_AVAILABLE = True
except ImportError:
    print("Warning: z3-solver not installed. Install with: pip install z3-solver")
    Z3_AVAILABLE = False


# Import CSP components (simplified version)
class DomainType(Enum):
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    BOOLEAN = "boolean"


@dataclass
class ColumnSchema:
    """Schema definition for a database column"""

    name: str
    dtype: DomainType
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    nullable: bool = False
    unique: bool = False
    categories: Optional[List[str]] = None


@dataclass
class SQLConstraint:
    """Represents a SQL WHERE clause constraint"""

    column: str
    operator: str  # =, !=, <, <=, >, >=, IN, LIKE, BETWEEN
    value: Any
    logic: str = "AND"  # AND, OR


class Z3ConstraintSolver:
    """Uses Z3 to check constraint satisfiability"""

    def __init__(self):
        if not Z3_AVAILABLE:
            raise ImportError(
                "z3-solver is required. Install with: pip install z3-solver"
            )
        self.solver = Solver()
        self.variables = {}

    def add_column(self, col_schema: ColumnSchema):
        """Add a column variable to Z3 solver"""
        if col_schema.dtype == DomainType.INTEGER:
            var = Int(col_schema.name)
            if col_schema.min_value is not None:
                self.solver.add(var >= col_schema.min_value)
            if col_schema.max_value is not None:
                self.solver.add(var <= col_schema.max_value)

        elif col_schema.dtype == DomainType.FLOAT:
            var = Real(col_schema.name)
            if col_schema.min_value is not None:
                self.solver.add(var >= col_schema.min_value)
            if col_schema.max_value is not None:
                self.solver.add(var <= col_schema.max_value)

        elif col_schema.dtype == DomainType.BOOLEAN:
            var = Bool(col_schema.name)

        elif col_schema.dtype == DomainType.STRING:
            # Z3 String theory (limited support)
            var = String(col_schema.name)

        else:
            # Fallback to Int for unknown types
            var = Int(col_schema.name)

        self.variables[col_schema.name] = var
        return var

    def add_constraint(self, constraint: SQLConstraint):
        """Add SQL constraint to Z3 solver"""
        if constraint.column not in self.variables:
            raise ValueError(f"Column {constraint.column} not defined")

        var = self.variables[constraint.column]

        # Map SQL operators to Z3 constraints
        if constraint.operator == "=":
            self.solver.add(var == constraint.value)
        elif constraint.operator == "!=":
            self.solver.add(var != constraint.value)
        elif constraint.operator == "<":
            self.solver.add(var < constraint.value)
        elif constraint.operator == "<=":
            self.solver.add(var <= constraint.value)
        elif constraint.operator == ">":
            self.solver.add(var > constraint.value)
        elif constraint.operator == ">=":
            self.solver.add(var >= constraint.value)
        elif constraint.operator == "IN":
            # Create OR constraint for multiple values
            or_constraints = [var == val for val in constraint.value]
            self.solver.add(Or(*or_constraints))
        elif constraint.operator == "BETWEEN":
            low, high = constraint.value
            self.solver.add(And(var >= low, var <= high))

    def is_satisfiable(self) -> bool:
        """Check if constraints are satisfiable"""
        result = self.solver.check()
        return result == sat

    def get_model(self) -> Optional[Dict[str, Any]]:
        """Get a satisfying assignment if one exists"""
        if self.solver.check() == sat:
            model = self.solver.model()
            result = {}
            for col_name, var in self.variables.items():
                val = model[var]
                if val is not None:
                    # Convert Z3 values to Python types
                    if isinstance(val, IntNumRef):
                        result[col_name] = val.as_long()
                    elif isinstance(val, RatNumRef):
                        result[col_name] = float(val.as_decimal(10))
                    elif isinstance(val, BoolRef):
                        result[col_name] = bool(val)
                    else:
                        result[col_name] = str(val)
            return result
        return None

    def get_refined_bounds(self, column: str) -> Tuple[Optional[Any], Optional[Any]]:
        """Get tighter bounds for a column based on constraints"""
        if column not in self.variables:
            return None, None

        var = self.variables[column]

        # Try to find minimum value
        self.solver.push()
        min_val = None
        if self.solver.check() == sat:
            model = self.solver.model()
            if var in model:
                val = model[var]
                if isinstance(val, IntNumRef):
                    min_val = val.as_long()
                elif isinstance(val, RatNumRef):
                    min_val = float(val.as_decimal(10))
        self.solver.pop()

        # Try to find maximum value (simplified)
        max_val = None
        # Note: Finding actual max would require optimization, skipping for simplicity

        return min_val, max_val


# CSP Propagation Components
class Variable:
    """Represents a CSP variable with a domain"""

    def __init__(self, name: str, domain: Set[Any]):
        self.name = name
        self.domain = domain.copy()
        self.initial_domain = domain.copy()

    def reset(self):
        self.domain = self.initial_domain.copy()


class CSPConstraint:
    """Base class for CSP constraints with propagation"""

    def __init__(self, variables: List[Variable]):
        self.variables = variables

    def is_satisfied(self, assignment: Dict[Variable, Any]) -> bool:
        """Check if constraint is satisfied"""
        raise NotImplementedError

    def propagate(self) -> bool:
        """Propagate constraint to reduce domains. Returns True if modified."""
        raise NotImplementedError


class UnaryCSPConstraint(CSPConstraint):
    """Unary constraint on a single variable"""

    def __init__(self, variable: Variable, predicate):
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


class BinaryCSPConstraint(CSPConstraint):
    """Binary constraint between two variables"""

    def __init__(self, var1: Variable, var2: Variable, predicate):
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


class AllDifferentCSPConstraint(CSPConstraint):
    """All variables must have different values"""

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

        # Remove assigned values from other variables
        for var in self.variables:
            if var not in assigned and len(var.domain) > 1:
                original_size = len(var.domain)
                var.domain -= set(assigned.values())
                if len(var.domain) < original_size:
                    modified = True
                if len(var.domain) == 0:
                    raise ValueError(f"Domain of {var.name} became empty!")

        return modified


class CSPSolver:
    """AC-3 constraint propagation solver"""

    def __init__(self):
        self.variables: List[Variable] = []
        self.constraints: List[CSPConstraint] = []

    def add_variable(self, variable: Variable):
        self.variables.append(variable)

    def add_constraint(self, constraint: CSPConstraint):
        self.constraints.append(constraint)

    def propagate(self) -> bool:
        """Apply AC-3 constraint propagation"""
        from collections import deque

        queue = deque(self.constraints)

        try:
            while queue:
                constraint = queue.popleft()
                if constraint.propagate():
                    # Re-add related constraints
                    for other in self.constraints:
                        if other != constraint and any(
                            v in constraint.variables for v in other.variables
                        ):
                            if other not in queue:
                                queue.append(other)
            return True
        except ValueError:
            return False

    def solve(self) -> Optional[Dict[Variable, Any]]:
        """Solve CSP with backtracking"""
        if not self.propagate():
            return None

        if all(len(var.domain) == 1 for var in self.variables):
            return {var: next(iter(var.domain)) for var in self.variables}

        return self._backtrack({})

    def _backtrack(
        self, assignment: Dict[Variable, Any]
    ) -> Optional[Dict[Variable, Any]]:
        if len(assignment) == len(self.variables):
            return assignment

        # MRV heuristic
        unassigned = [v for v in self.variables if v not in assignment]
        var = min(unassigned, key=lambda v: len(v.domain))

        for value in list(var.domain):
            if self._is_consistent(var, value, assignment):
                assignment[var] = value
                saved_domains = {v: v.domain.copy() for v in self.variables}

                var.domain = {value}
                if self.propagate():
                    result = self._backtrack(assignment)
                    if result is not None:
                        return result

                for v in self.variables:
                    v.domain = saved_domains[v]
                del assignment[var]

        return None

    def _is_consistent(
        self, var: Variable, value: Any, assignment: Dict[Variable, Any]
    ) -> bool:
        assignment[var] = value
        consistent = all(c.is_satisfied(assignment) for c in self.constraints)
        del assignment[var]
        return consistent


class CSPDataGenerator:
    """Generates realistic data using constraint propagation"""

    def __init__(self, schema: List[ColumnSchema], verbose: bool = False):
        self.schema = {col.name: col for col in schema}
        self.sql_constraints = []
        self.verbose = verbose

    def add_constraint(self, constraint: SQLConstraint):
        """Add constraint for data generation"""
        self.sql_constraints.append(constraint)

    def generate_domain(
        self, col_schema: ColumnSchema, refined_bounds: Tuple = (None, None)
    ) -> Set[Any]:
        """Generate domain values for a column"""
        min_val, max_val = refined_bounds

        if col_schema.dtype == DomainType.INTEGER:
            min_v = min_val if min_val is not None else (col_schema.min_value or 0)
            max_v = max_val if max_val is not None else (col_schema.max_value or 100)

            if max_v - min_v <= 50:
                return set(range(int(min_v), int(max_v) + 1))
            else:
                step = max(1, (max_v - min_v) // 30)
                return set(range(int(min_v), int(max_v) + 1, step))

        elif col_schema.dtype == DomainType.FLOAT:
            min_v = min_val if min_val is not None else (col_schema.min_value or 0.0)
            max_v = max_val if max_val is not None else (col_schema.max_value or 100.0)
            step = (max_v - min_v) / 20
            return {round(min_v + i * step, 2) for i in range(21)}

        elif col_schema.dtype == DomainType.BOOLEAN:
            return {True, False}

        elif col_schema.dtype == DomainType.STRING:
            if col_schema.categories:
                return set(col_schema.categories)
            return {f"val_{i}" for i in range(10)}

        return {0, 1, 2, 3, 4, 5}

    def generate_rows(self, num_rows: int) -> List[Dict[str, Any]]:
        """Generate rows using CSP propagation"""
        rows = []

        for row_idx in range(num_rows):
            if self.verbose:
                print(f"  Generating row {row_idx + 1}/{num_rows}...")

            # Create CSP for this row
            solver = CSPSolver()
            variables = {}

            # Create variables with initial domains
            for col_name, col_schema in self.schema.items():
                domain = self.generate_domain(col_schema)
                var = Variable(f"{col_name}_R{row_idx}", domain)
                variables[col_name] = var
                solver.add_variable(var)

            # Add unary constraints from SQL
            for sql_constraint in self.sql_constraints:
                col_name = sql_constraint.column
                if col_name not in variables:
                    continue

                var = variables[col_name]
                predicate = self._create_predicate(sql_constraint)

                if predicate:
                    solver.add_constraint(UnaryCSPConstraint(var, predicate))

            # Add uniqueness constraints if needed
            if row_idx > 0:
                for col_name, col_schema in self.schema.items():
                    if col_schema.unique:
                        # Ensure this value differs from previous rows
                        used_values = {rows[i][col_name] for i in range(len(rows))}
                        var = variables[col_name]
                        solver.add_constraint(
                            UnaryCSPConstraint(
                                var, lambda x, used=used_values: x not in used
                            )
                        )

            # Solve CSP for this row
            solution = solver.solve()

            if solution:
                row_data = {col: solution[var] for col, var in variables.items()}
                rows.append(row_data)
                if self.verbose:
                    print(f"    ✓ Row generated: {row_data}")
            else:
                if self.verbose:
                    print(f"    ✗ Failed to generate row {row_idx + 1}")
                break

        return rows

    def _create_predicate(self, constraint: SQLConstraint):
        """Create a predicate function from SQL constraint"""
        op = constraint.operator
        val = constraint.value

        if op == "=":
            return lambda x: x == val
        elif op == "!=":
            return lambda x: x != val
        elif op == "<":
            return lambda x: x < val
        elif op == "<=":
            return lambda x: x <= val
        elif op == ">":
            return lambda x: x > val
        elif op == ">=":
            return lambda x: x >= val
        elif op == "IN":
            return lambda x: x in val
        elif op == "BETWEEN":
            low, high = val
            return lambda x: low <= x <= high

        return None


class HybridSQLExecutor:
    """
    Hybrid executor combining Z3 and CSP
    - Uses Z3 to validate constraint satisfiability
    - Uses CSP to generate realistic test data
    """

    def __init__(self, schema: List[ColumnSchema], verbose: bool = True):
        self.schema = schema
        self.verbose = verbose

    def execute_query(
        self, constraints: List[SQLConstraint], num_rows: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Execute symbolic query with hybrid approach

        Returns:
            List of generated rows if satisfiable, None otherwise
        """
        if self.verbose:
            print("=" * 60)
            print("HYBRID SQL SYMBOLIC EXECUTION")
            print("=" * 60)

        # Phase 1: Use Z3 to check satisfiability
        if self.verbose:
            print("\n[Phase 1] Z3 Constraint Validation...")

        try:
            z3_solver = Z3ConstraintSolver()

            # Add schema to Z3
            for col in self.schema:
                z3_solver.add_column(col)

            # Add constraints to Z3
            for constraint in constraints:
                z3_solver.add_constraint(constraint)

            # Check satisfiability
            if not z3_solver.is_satisfiable():
                if self.verbose:
                    print(
                        "  ✗ UNSATISFIABLE - No valid data exists for these constraints"
                    )
                return None

            if self.verbose:
                print("  ✓ SATISFIABLE - Constraints can be satisfied")

                # Show example from Z3
                example = z3_solver.get_model()
                if example:
                    print("  Example solution from Z3:")
                    for col, val in example.items():
                        print(f"    {col} = {val}")

        except Exception as e:
            if self.verbose:
                print(f"  ⚠ Z3 validation failed: {e}")
                print("  Proceeding with CSP generation anyway...")

        # Phase 2: Use CSP to generate realistic data
        if self.verbose:
            print("\n[Phase 2] CSP Data Generation with Constraint Propagation...")

        generator = CSPDataGenerator(self.schema, verbose=self.verbose)

        # Add constraints to CSP
        for constraint in constraints:
            generator.add_constraint(constraint)

        # Generate rows
        rows = generator.generate_rows(num_rows)

        if self.verbose:
            if rows:
                print(f"  ✓ Generated {len(rows)} valid rows")
            else:
                print("  ✗ Failed to generate valid rows")

        return rows if rows else None

    def analyze_query(self, constraints: List[SQLConstraint]) -> Dict[str, Any]:
        """
        Analyze query constraints and provide insights
        """
        analysis = {
            "satisfiable": False,
            "example_solution": None,
            "tight_bounds": {},
            "constraint_count": len(constraints),
            "columns_involved": set(),
        }

        if not Z3_AVAILABLE:
            return analysis

        try:
            z3_solver = Z3ConstraintSolver()

            for col in self.schema:
                z3_solver.add_column(col)

            for constraint in constraints:
                z3_solver.add_constraint(constraint)
                analysis["columns_involved"].add(constraint.column)

            analysis["satisfiable"] = z3_solver.is_satisfiable()
            analysis["example_solution"] = z3_solver.get_model()

            # Get refined bounds for numeric columns
            for col in self.schema:
                if col.dtype in [DomainType.INTEGER, DomainType.FLOAT]:
                    bounds = z3_solver.get_refined_bounds(col.name)
                    analysis["tight_bounds"][col.name] = bounds

        except Exception as e:
            analysis["error"] = str(e)

        return analysis


# Example Usage
if __name__ == "__main__":
    print("HYBRID SQL SYMBOLIC EXECUTOR - Demo\n")

    # Define database schema
    schema = [
        ColumnSchema(
            "employee_id",
            DomainType.INTEGER,
            min_value=1000,
            max_value=9999,
            unique=True,
        ),
        ColumnSchema("age", DomainType.INTEGER, min_value=18, max_value=65),
        ColumnSchema("salary", DomainType.FLOAT, min_value=30000.0, max_value=200000.0),
        ColumnSchema(
            "department",
            DomainType.STRING,
            categories=["Engineering", "Sales", "HR", "Finance"],
        ),
        ColumnSchema("is_manager", DomainType.BOOLEAN),
    ]

    # Example 1: Simple query
    print("Example 1: Simple Range Query")
    print("-" * 60)
    print("SQL: SELECT * FROM employees WHERE age > 30 AND salary < 100000")

    constraints1 = [
        SQLConstraint("age", ">", 30),
        SQLConstraint("salary", "<", 100000.0),
    ]

    executor = HybridSQLExecutor(schema, verbose=True)
    rows = executor.execute_query(constraints1, num_rows=5)

    if rows:
        print("\nGenerated Data:")
        print(f"{'ID':<8} {'Age':<6} {'Salary':<12} {'Dept':<15} {'Manager':<8}")
        print("-" * 60)
        for row in rows:
            print(
                f"{row['employee_id']:<8} {row['age']:<6} ${row['salary']:<11,.2f} "
                f"{row['department']:<15} {row['is_manager']:<8}"
            )

    # Example 2: Complex query with multiple constraints
    print("\n" + "=" * 60)
    print("Example 2: Complex Query")
    print("-" * 60)
    print("SQL: SELECT * FROM employees WHERE")
    print("  department IN ('Engineering', 'Sales') AND")
    print("  age BETWEEN 25 AND 40 AND")
    print("  salary >= 50000")

    constraints2 = [
        SQLConstraint("department", "IN", ["Engineering", "Sales"]),
        SQLConstraint("age", "BETWEEN", (25, 40)),
        SQLConstraint("salary", ">=", 50000.0),
    ]

    executor2 = HybridSQLExecutor(schema, verbose=True)
    rows2 = executor2.execute_query(constraints2, num_rows=5)

    if rows2:
        print("\nGenerated Data:")
        print(f"{'ID':<8} {'Age':<6} {'Salary':<12} {'Dept':<15} {'Manager':<8}")
        print("-" * 60)
        for row in rows2:
            print(
                f"{row['employee_id']:<8} {row['age']:<6} ${row['salary']:<11,.2f} "
                f"{row['department']:<15} {row['is_manager']:<8}"
            )

    # Example 3: Unsatisfiable query
    print("\n" + "=" * 60)
    print("Example 3: Unsatisfiable Query")
    print("-" * 60)
    print("SQL: SELECT * FROM employees WHERE age > 70")

    constraints3 = [SQLConstraint("age", ">", 70)]  # Impossible: max age is 65

    executor3 = HybridSQLExecutor(schema, verbose=True)
    rows3 = executor3.execute_query(constraints3, num_rows=5)

    # Example 4: Query Analysis
    print("\n" + "=" * 60)
    print("Example 4: Query Analysis")
    print("-" * 60)

    analysis = executor.analyze_query(constraints2)
    print(f"Satisfiable: {analysis['satisfiable']}")
    print(f"Constraints: {analysis['constraint_count']}")
    print(f"Columns involved: {analysis['columns_involved']}")
    if analysis["example_solution"]:
        print("Example solution:")
        for col, val in analysis["example_solution"].items():
            print(f"  {col} = {val}")
