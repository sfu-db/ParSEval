import random
import string
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional


# -------------------------------
# Basic DataType abstraction
# -------------------------------
class DataType:
    def __init__(self, dtype: str):
        self.dtype = dtype.upper()

    @classmethod
    def build(cls, dtype):
        return cls(dtype)

    def is_numeric(self):
        return self.dtype in ("INT", "FLOAT", "DOUBLE", "DECIMAL")

    def is_integer(self):
        return self.dtype in ("INT", "BIGINT")

    def is_float(self):
        return self.dtype in ("FLOAT", "DOUBLE", "DECIMAL")

    def is_string(self):
        return self.dtype in ("STRING", "VARCHAR", "CHAR")

    def is_boolean(self):
        return self.dtype == "BOOLEAN"

    def is_datetime(self):
        return self.dtype in ("DATE", "DATETIME", "TIMESTAMP")


# -------------------------------
# ColumnRef for logical references
# -------------------------------
class ColumnRef:
    def __init__(self, table, column):
        self.table = table
        self.column = column
        self.qualified_name = f"{table}.{column}"

    def __repr__(self):
        return f"ColumnRef({self.qualified_name})"


# -------------------------------
# ColumnDomain
# -------------------------------
class ColumnDomain:
    def __init__(
        self,
        table_name: str,
        column_name: str,
        datatype: str,
        min_val=None,
        max_val=None,
        unique=False,
        target_type=None,
    ):
        self.table_name = table_name
        self.column_name = column_name
        self.datatype = DataType.build(datatype)
        self.target_type = DataType.build(target_type) if target_type else self.datatype
        self.min_val = min_val
        self.max_val = max_val
        self.unique = unique

    @property
    def name(self):
        return f"{self.table_name}.{self.column_name}"

    def __repr__(self):
        return f"ColumnDomain({self.name})"


# -------------------------------
# ValuePool
# -------------------------------
class ValuePool:
    """
    A pool of values for a column or a cluster of joined columns.
    Handles uniqueness, exclusion, CAST type, and PK/FK propagation.
    """

    def __init__(self, domain: ColumnDomain):
        self.domains: List[ColumnDomain] = [domain]
        self.values: List[Any] = []
        self.excluded: set = set()
        self.min_val = domain.min_val
        self.max_val = domain.max_val
        self.target_type = domain.target_type

    def add_domain(self, domain: ColumnDomain):
        self.domains.append(domain)
        if domain.min_val is not None:
            self.min_val = min(self.min_val or domain.min_val, domain.min_val)
        if domain.max_val is not None:
            self.max_val = max(self.max_val or domain.max_val, domain.max_val)
        if domain.target_type:
            self.target_type = domain.target_type

    def generate_value_for_column(self, domain: ColumnDomain):
        dtype = self.target_type
        # Unique column: generate a new value not in values
        existing_values = [v for v in self.values if v not in self.excluded]
        if domain.unique:
            candidates = (
                set(range(self.min_val or 0, (self.max_val or 10000) + 1))
                - set(self.values)
                - self.excluded
            )
            if not candidates:
                raise ValueError(f"No unique value left for {domain.name}")
            value = random.choice(list(candidates))
        else:
            # Non-unique: reuse existing if available
            if existing_values:
                value = random.choice(existing_values)
            else:
                value = self._generate_new_value(dtype)

        self.values.append(value)
        return value

    def _generate_new_value(self, dtype):
        if dtype.is_integer():
            return random.randint(self.min_val or 0, self.max_val or 10000)
        elif dtype.is_float():
            return round(random.uniform(self.min_val or 0.0, self.max_val or 1.0), 4)
        elif dtype.is_string():
            chars = string.ascii_letters + string.digits
            return "".join(random.choices(chars, 10))
        elif dtype.is_boolean():
            return random.choice([True, False])
        elif dtype.is_datetime():
            start_date = datetime.now() - timedelta(days=365 * 10)
            return start_date + timedelta(days=random.randint(0, 3650))
        else:
            return random.randint(self.min_val or 0, self.max_val or 10000)

    def add_excluded(self, values: List[Any]):
        self.excluded.update(values)

    def propagate_bounds(self, min_val=None, max_val=None):
        if min_val is not None:
            self.min_val = max(self.min_val or min_val, min_val)
        if max_val is not None:
            self.max_val = min(self.max_val or max_val, max_val)

    def get_latest(self):
        return (
            self.values[-1]
            if self.values
            else self._generate_new_value(self.target_type)
        )


# -------------------------------
# ColumnDomainPool
# -------------------------------
class ColumnDomainPool:
    """
    Manages all columns and value pools.
    Handles joins, foreign keys, and cluster merging.
    """

    def __init__(self):
        self.domains: Dict[str, ColumnDomain] = {}
        self.pools: Dict[str, ValuePool] = {}

    def register_domain(self, domain: ColumnDomain):
        self.domains[domain.name] = domain
        self.pools[domain.name] = ValuePool(domain)

    def link_columns(self, col1_name: str, col2_name: str):
        """Merge pools for join/equality constraints."""
        pool1 = self.pools[col1_name]
        pool2 = self.pools[col2_name]
        if pool1 is pool2:
            return
        # merge pool2 into pool1
        for domain in pool2.domains:
            pool1.add_domain(domain)
            self.pools[domain.name] = pool1

    def get_pool(self, col_name: str):
        return self.pools.get(col_name)

    def get_domain(self, col_name: str):
        return self.domains.get(col_name)

    def show_summary(self):
        print("=== ColumnDomainPool Summary ===")
        for name, pool in self.pools.items():
            root = pool.domains[0].name
            print(
                f"{name:25} → root={root:25} values={pool.values} unique={pool.domains[0].unique}"
            )


# -------------------------------
# HybridSolver
# -------------------------------
class HybridSolver:
    """
    HybridSolver generates consistent values for SQL columns based on constraints.
    """

    def __init__(self, pool: ColumnDomainPool):
        self.pool = pool

    def solve(self, constraints: List[tuple]) -> Dict[Any, Any]:
        """
        Solve constraints and generate consistent values.

        constraints: list of (left, op, right)
        """
        # 1. Group constraints
        grouped: Dict[str, List[tuple]] = {}
        for left, op, right in constraints:
            for col in [left, right]:
                if isinstance(col, ColumnRef):
                    grouped.setdefault(col.qualified_name, []).append((left, op, right))

        # 2. Propagate equality (joins)

        for col_name, constraints in grouped.items():
            print(f"processing constraint {constraints}")
            for left, op, right in constraints:
                pool = self.pool.get_pool(col_name)
                if pool is None:
                    continue

                # Equality join
                if op == "=" and isinstance(right, ColumnRef):
                    self.pool.link_columns(left.qualified_name, right.qualified_name)

                # Range propagation
                elif op in ("<", "<=", ">", ">="):
                    min_val, max_val = None, None
                    if op == "<":
                        max_val = right
                    elif op == "<=":
                        max_val = right
                    elif op == ">":
                        min_val = right
                    elif op == ">=":
                        min_val = right
                    pool.propagate_bounds(min_val, max_val)

                # Not-equal propagation
                elif op == "!=":
                    print(
                        f"exclude {right} from {col_name} ******************************************************************"
                    )
                    pool.add_excluded([right])

        for left, op, right in constraints:
            if (
                op == "="
                and isinstance(left, ColumnRef)
                and isinstance(right, ColumnRef)
            ):
                self.pool.link_columns(left.qualified_name, right.qualified_name)

        # 3. Generate values
        assignments: Dict[Any, Any] = {}
        for col_name, cons in grouped.items():
            domain = self.pool.get_domain(col_name)
            pool = self.pool.get_pool(col_name)
            val = pool.generate_value_for_column(domain)
            assignments[domain] = val

        return assignments

    def show_pools(self):
        self.pool.show_summary()


# Create Pool
pool = ColumnDomainPool()
pool.register_domain(ColumnDomain("Customers", "id", "INT", unique=True))
pool.register_domain(
    ColumnDomain("Orders", "customer_id", "INT", target_type="FLOAT", unique=False)
)

# Solver
solver = HybridSolver(pool)

# Define constraints
cust_id = ColumnRef("Customers", "id")
order_cust = ColumnRef("Orders", "customer_id")
constraints = [(cust_id, "=", order_cust), (cust_id, "<", 10)]  # join  # filter

# Generate multiple assignments
for i in range(5):
    assignments = solver.solve(constraints)
    print(f"Iteration {i+1}: Assignments:", assignments)

# Show pool states
solver.show_pools()
