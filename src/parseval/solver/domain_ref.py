import random
from collections import defaultdict
from typing import Dict, Set, Optional, List, Callable, Any, Tuple
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ValuePool:
    """Stores generated values for a column domain, ensuring consistency."""

    def __init__(self, unique=False):
        self.values: Set[Any] = set()
        self.unique = unique
        self.locked = False  # once used by dependent domains
        self.generation_count = 0  # track how many times we've generated

    def add(self, value):
        """Add a value to the pool."""
        self.values.add(value)
        self.generation_count += 1

    def sample(self, count: int = 1) -> List[Any]:
        """Sample values from the pool (with replacement)."""
        if not self.values:
            return []
        return random.choices(list(self.values), k=count)

    def mark_locked(self):
        """Lock the pool when another column depends on it (e.g., FK reference)."""
        if not self.locked:
            self.locked = True
            logger.info(f"🔒 Pool locked with {len(self.values)} values")

    def has_values(self) -> bool:
        """Check if pool has any values."""
        return len(self.values) > 0


class CompositeValuePool:
    """Stores composite key tuples, ensuring uniqueness across multiple columns."""

    def __init__(self):
        self.tuples: Set[Tuple] = set()  # Set of tuples like (category, sku)
        self.locked = False
        self.generation_count = 0

    def add(self, value_tuple: Tuple):
        """Add a composite value tuple."""
        self.tuples.add(value_tuple)
        self.generation_count += 1

    def sample(self, count: int = 1) -> List[Tuple]:
        """Sample composite tuples from the pool."""
        if not self.tuples:
            return []
        return random.choices(list(self.tuples), k=count)

    def mark_locked(self):
        """Lock the composite pool."""
        if not self.locked:
            self.locked = True
            logger.info(f"🔒 Composite pool locked with {len(self.tuples)} tuples")

    def has_values(self) -> bool:
        """Check if pool has any tuples."""
        return len(self.tuples) > 0


class ColumnDomain:
    """Defines how to generate values for a column."""

    def __init__(
        self,
        name: str,
        generator: Callable[[], Any],
        unique: bool = False,
        nullable: bool = True,
        is_primary_key: bool = False,
    ):
        self.name = name
        self.generator = generator
        self.unique = unique
        self.nullable = nullable
        self.is_primary_key = is_primary_key

        # Primary keys must be unique and non-nullable
        if is_primary_key:
            self.unique = True
            self.nullable = False

    def generate(self) -> Any:
        """Generate a single value using the generator function."""
        return self.generator()


class ColumnDomainPool:
    """Manages all column domains and their value generation, including composite FKs."""

    def __init__(self):
        self.domains: Dict[str, ColumnDomain] = {}
        self.value_pools: Dict[str, ValuePool] = {}

        # Simple FK: maps FK column -> referenced column
        self.fk_links: Dict[str, str] = {}

        # Composite FK: maps tuple of FK columns -> tuple of referenced columns
        self.composite_fk_links: Dict[Tuple[str, ...], Tuple[str, ...]] = {}

        # Composite pools: maps tuple of columns -> CompositeValuePool
        self.composite_pools: Dict[Tuple[str, ...], CompositeValuePool] = {}

        # Track what references each column/composite
        self.referenced_by: Dict[str, List[str]] = defaultdict(list)
        self.composite_referenced_by: Dict[Tuple[str, ...], List[Tuple[str, ...]]] = (
            defaultdict(list)
        )

    def register_domain(self, domain: ColumnDomain):
        """Register a column domain."""
        key = domain.name
        self.domains[key] = domain
        self.value_pools[key] = ValuePool(unique=domain.unique)

        pk_label = " (PRIMARY KEY)" if domain.is_primary_key else ""
        unique_label = (
            " (UNIQUE)" if domain.unique and not domain.is_primary_key else ""
        )
        logger.info(f"Registered domain: {key}{pk_label}{unique_label}")

    def register_composite_unique(self, columns: List[str]):
        """
        Register a composite UNIQUE constraint across multiple columns.
        Required before creating composite foreign keys.
        """
        col_tuple = tuple(columns)

        # Validate all columns exist
        for col in columns:
            if col not in self.domains:
                raise ValueError(f"Column {col} not registered")

        # Create composite pool
        if col_tuple not in self.composite_pools:
            self.composite_pools[col_tuple] = CompositeValuePool()
            logger.info(f"Registered composite UNIQUE constraint: {columns}")

    def link_foreign_key(self, fk_col: str, referenced_col: str):
        """
        Link single-column FK to reference another column's value pool.

        Args:
            fk_col: Foreign key column name (e.g., "A.b_id")
            referenced_col: Referenced column name (e.g., "B.id")
                           Must have unique=True constraint
        """
        if referenced_col not in self.value_pools:
            raise ValueError(
                f"Cannot link: referenced column {referenced_col} not found"
            )

        if fk_col not in self.domains:
            raise ValueError(f"Foreign key column {fk_col} not registered")

        # Validate that referenced column is unique
        ref_domain = self.domains[referenced_col]
        if not ref_domain.unique:
            raise ValueError(
                f"Cannot create foreign key: referenced column {referenced_col} "
                f"must be UNIQUE or PRIMARY KEY. Current: unique={ref_domain.unique}"
            )

        # Store the link
        self.fk_links[fk_col] = referenced_col
        self.referenced_by[referenced_col].append(fk_col)

        # Lock the referenced pool
        self.value_pools[referenced_col].mark_locked()

        ref_type = "PRIMARY KEY" if ref_domain.is_primary_key else "UNIQUE column"
        logger.info(f"Linked FK: {fk_col} → {referenced_col} ({ref_type})")

    def link_composite_foreign_key(
        self, fk_columns: List[str], referenced_columns: List[str]
    ):
        """
        Link composite FK (multiple columns) to reference a composite unique constraint.

        Args:
            fk_columns: List of FK column names (e.g., ["Inventory.category", "Inventory.sku"])
            referenced_columns: List of referenced columns (e.g., ["Products.category", "Products.sku"])
                               Must have a composite UNIQUE constraint registered

        Example:
            # Products has UNIQUE(category, sku)
            pool.register_composite_unique(["Products.category", "Products.sku"])

            # Inventory references it
            pool.link_composite_foreign_key(
                ["Inventory.product_category", "Inventory.product_sku"],
                ["Products.category", "Products.sku"]
            )
        """
        if len(fk_columns) != len(referenced_columns):
            raise ValueError("FK and referenced column counts must match")

        fk_tuple = tuple(fk_columns)
        ref_tuple = tuple(referenced_columns)

        # Validate all columns exist
        for col in fk_columns + referenced_columns:
            if col not in self.domains:
                raise ValueError(f"Column {col} not registered")

        # Validate composite unique constraint exists
        if ref_tuple not in self.composite_pools:
            raise ValueError(
                f"No composite UNIQUE constraint found for {referenced_columns}. "
                f"Call register_composite_unique() first."
            )

        # Store the link
        self.composite_fk_links[fk_tuple] = ref_tuple
        self.composite_referenced_by[ref_tuple].append(fk_tuple)

        # Lock the composite pool
        self.composite_pools[ref_tuple].mark_locked()

        logger.info(f"Linked composite FK: {fk_columns} → {referenced_columns}")

    def generate_values(self, col_name: str, count: int) -> List[Any]:
        """Generate values for a single column."""
        if col_name not in self.domains:
            raise ValueError(f"Column {col_name} not registered")

        # Check if this is a simple FK column
        if col_name in self.fk_links:
            return self._generate_fk_values(col_name, count)

        # Check if this column is part of a composite FK
        for fk_tuple in self.composite_fk_links.keys():
            if col_name in fk_tuple:
                raise ValueError(
                    f"Column {col_name} is part of composite FK {list(fk_tuple)}. "
                    f"Use generate_composite_values() instead."
                )

        # Regular column generation
        return self._generate_regular_values(col_name, count)

    def generate_composite_values(
        self,
        columns: List[str],
        count: int,
        generators: Optional[List[Callable]] = None,
    ) -> List[Tuple[Any, ...]]:
        """
        Generate composite values (tuples) for multiple columns with UNIQUE constraint.

        Args:
            columns: List of column names that form a composite key
            count: Number of tuples to generate
            generators: Optional list of generator functions (one per column)
                       Not needed for composite FK columns

        Returns:
            List of tuples, where each tuple contains values for the columns
        """
        col_tuple = tuple(columns)

        # Check if this is a composite FK
        if col_tuple in self.composite_fk_links:
            return self._generate_composite_fk_values(col_tuple, count)

        # Check if composite unique constraint exists
        if col_tuple not in self.composite_pools:
            raise ValueError(
                f"No composite constraint registered for {columns}. "
                f"Call register_composite_unique() first."
            )

        # Generate regular composite values
        return self._generate_composite_regular_values(col_tuple, count, generators)

    def _generate_composite_regular_values(
        self,
        col_tuple: Tuple[str, ...],
        count: int,
        generators: Optional[List[Callable]],
    ) -> List[Tuple[Any, ...]]:
        """Generate composite unique values."""
        composite_pool = self.composite_pools[col_tuple]

        if generators is None:
            generators = [self.domains[col].generate for col in col_tuple]

        if len(generators) != len(col_tuple):
            raise ValueError("Number of generators must match number of columns")

        # Warn if locked
        if composite_pool.locked:
            dependent_fks = self.composite_referenced_by.get(col_tuple, [])
            logger.warning(
                f"⚠️  Generating values for LOCKED composite {list(col_tuple)}. "
                f"Referenced by: {[list(fk) for fk in dependent_fks]}"
            )

        tuples = []
        attempts = 0
        max_attempts = count * 1000

        while len(tuples) < count and attempts < max_attempts:
            # Generate tuple
            value_tuple = tuple(gen() for gen in generators)

            # Check uniqueness
            if value_tuple not in composite_pool.tuples:
                tuples.append(value_tuple)
                composite_pool.add(value_tuple)

                # Also add individual values to their pools
                for col, val in zip(col_tuple, value_tuple):
                    self.value_pools[col].add(val)

            attempts += 1

        # Handle exhaustion
        if len(tuples) < count:
            shortage = count - len(tuples)
            logger.error(
                f"❌ Composite domain exhausted for {list(col_tuple)}! "
                f"Generated {len(tuples)}/{count} unique tuples. "
                f"Filling with duplicates..."
            )
            if composite_pool.tuples:
                duplicates = composite_pool.sample(shortage)
                tuples.extend(duplicates)

        logger.info(f"Generated {len(tuples)} composite tuples for {list(col_tuple)}")
        return tuples

    def _generate_composite_fk_values(
        self, fk_tuple: Tuple[str, ...], count: int
    ) -> List[Tuple[Any, ...]]:
        """Generate composite FK values by sampling from referenced composite pool."""
        ref_tuple = self.composite_fk_links[fk_tuple]
        ref_pool = self.composite_pools[ref_tuple]

        # Unified validation and sampling
        tuples = self._sample_from_referenced_pool(
            fk_identifier=list(fk_tuple),
            ref_identifier=list(ref_tuple),
            ref_pool=ref_pool,
            count=count,
            is_composite=True,
        )

        # Track in individual FK column pools
        for i, col in enumerate(fk_tuple):
            for tup in tuples:
                self.value_pools[col].add(tup[i])

        return tuples

    def _generate_regular_values(self, col_name: str, count: int) -> List[Any]:
        """Generate values for regular (non-FK) columns."""
        domain = self.domains[col_name]
        pool = self.value_pools[col_name]

        # Warn if locked
        if pool.locked:
            dependent_fks = self.referenced_by.get(col_name, [])
            logger.warning(
                f"⚠️  Generating values for LOCKED column {col_name}. "
                f"Referenced by: {dependent_fks}"
            )

        values = []
        attempts = 0
        max_attempts = count * 1000 if domain.unique else count

        while len(values) < count and attempts < max_attempts:
            value = domain.generate()

            if domain.unique:
                if value not in pool.values:
                    values.append(value)
                    pool.add(value)
                else:
                    attempts += 1
            else:
                values.append(value)
                pool.add(value)

            attempts += 1

        # Handle exhaustion
        if len(values) < count:
            shortage = count - len(values)
            logger.error(f"❌ Domain exhausted for {col_name}!")
            if pool.values:
                duplicates = pool.sample(shortage)
                values.extend(duplicates)

        logger.info(f"Generated {len(values)} values for {col_name}")
        return values

    def _generate_fk_values(self, fk_col: str, count: int) -> List[Any]:
        """Generate FK values by sampling from referenced column's pool."""
        referenced_col = self.fk_links[fk_col]
        ref_pool = self.value_pools[referenced_col]

        # Unified validation and sampling
        values = self._sample_from_referenced_pool(
            fk_identifier=fk_col,
            ref_identifier=referenced_col,
            ref_pool=ref_pool,
            count=count,
            is_composite=False,
        )

        # Track in FK pool
        fk_pool = self.value_pools[fk_col]
        for v in values:
            fk_pool.add(v)

        return values

    def _sample_from_referenced_pool(
        self,
        fk_identifier: Any,
        ref_identifier: Any,
        ref_pool: Any,  # ValuePool or CompositeValuePool
        count: int,
        is_composite: bool,
    ) -> List[Any]:
        """
        Unified method to sample from referenced pools (simple or composite).

        Args:
            fk_identifier: FK column name or list of columns
            ref_identifier: Referenced column name or list of columns
            ref_pool: ValuePool or CompositeValuePool
            count: Number of values/tuples to sample
            is_composite: True if composite FK, False if simple FK

        Returns:
            List of values (simple FK) or list of tuples (composite FK)
        """
        # Ensure referenced pool has values
        if not ref_pool.has_values():
            fk_str = (
                str(fk_identifier) if not is_composite else str(list(fk_identifier))
            )
            ref_str = (
                str(ref_identifier) if not is_composite else str(list(ref_identifier))
            )
            raise ValueError(
                f"Cannot generate FK values for {fk_str}: "
                f"referenced {ref_str} has no values yet. "
                f"Generate referenced values first!"
            )

        # Sample from referenced pool
        values = ref_pool.sample(count)

        # Log appropriately
        if is_composite:
            ref_domain = self.domains[ref_identifier[0]]  # Check first column
            logger.info(
                f"Generated {len(values)} composite FK tuples for {list(fk_identifier)} "
                f"from {list(ref_identifier)} pool (size={len(ref_pool.tuples)})"
            )
        else:
            ref_domain = self.domains[ref_identifier]
            ref_type = "PK" if ref_domain.is_primary_key else "UNIQUE"
            logger.info(
                f"Generated {len(values)} FK values for {fk_identifier} "
                f"from {ref_identifier} ({ref_type}) pool (size={len(ref_pool.values)})"
            )

        return values

    def print_summary(self):
        """Print a summary of all domains."""
        print("\n" + "=" * 70)
        print("COLUMN DOMAIN POOL SUMMARY")
        print("=" * 70)

        print("\n📊 Simple Columns:")
        for col_name in sorted(self.domains.keys()):
            domain = self.domains[col_name]
            pool = self.value_pools[col_name]

            labels = []
            if domain.is_primary_key:
                labels.append("PK")
            elif domain.unique:
                labels.append("UNIQUE")
            if pool.locked:
                labels.append("LOCKED")

            label_str = f" [{', '.join(labels)}]" if labels else ""
            print(f"  {col_name}{label_str}: {len(pool.values)} values")

        if self.composite_pools:
            print("\n📊 Composite Constraints:")
            for cols, pool in self.composite_pools.items():
                locked = " [LOCKED]" if pool.locked else ""
                print(f"  UNIQUE{list(cols)}{locked}: {len(pool.tuples)} tuples")

        if self.fk_links:
            print("\n🔗 Simple Foreign Keys:")
            for fk, ref in self.fk_links.items():
                print(f"  {fk} → {ref}")

        if self.composite_fk_links:
            print("\n🔗 Composite Foreign Keys:")
            for fk_tuple, ref_tuple in self.composite_fk_links.items():
                print(f"  {list(fk_tuple)} → {list(ref_tuple)}")


# Demonstration
def demo():
    """Demonstrate composite foreign keys."""
    print("\n" + "=" * 70)
    print("COMPOSITE FOREIGN KEY DEMONSTRATION")
    print("=" * 70)

    pool = ColumnDomainPool()

    print("\n📋 Scenario:")
    print("  Products: category + sku (composite UNIQUE)")
    print("  Inventory: product_category + product_sku (composite FK)")

    # Register Products table
    print("\n" + "-" * 70)
    print("Step 1: Register Products table with composite UNIQUE")
    print("-" * 70)

    pool.register_domain(
        ColumnDomain(
            name="Products.category",
            generator=lambda: random.choice(["Electronics", "Clothing", "Books"]),
            unique=False,
        )
    )

    pool.register_domain(
        ColumnDomain(
            name="Products.sku",
            generator=lambda: f"SKU-{random.randint(1000, 9999)}",
            unique=False,
        )
    )

    # Register composite UNIQUE constraint
    pool.register_composite_unique(["Products.category", "Products.sku"])

    # Register Inventory table
    print("\n" + "-" * 70)
    print("Step 2: Register Inventory table with composite FK")
    print("-" * 70)

    pool.register_domain(
        ColumnDomain(name="Inventory.product_category", generator=lambda: None)
    )

    pool.register_domain(
        ColumnDomain(name="Inventory.product_sku", generator=lambda: None)
    )

    # Link composite FK
    pool.link_composite_foreign_key(
        ["Inventory.product_category", "Inventory.product_sku"],
        ["Products.category", "Products.sku"],
    )

    # Generate Products data
    print("\n" + "-" * 70)
    print("Step 3: Generate Products composite values")
    print("-" * 70)

    product_tuples = pool.generate_composite_values(
        ["Products.category", "Products.sku"], count=5
    )
    print("Products (category, sku):")
    for tup in product_tuples:
        print(f"  {tup}")

    # Generate Inventory data
    print("\n" + "-" * 70)
    print("Step 4: Generate Inventory composite FK values")
    print("-" * 70)

    inventory_tuples = pool.generate_composite_values(
        ["Inventory.product_category", "Inventory.product_sku"], count=8
    )
    print("Inventory (product_category, product_sku):")
    for tup in inventory_tuples:
        print(f"  {tup}")

    print(
        f"\n✓ All FK tuples exist in Products: {set(inventory_tuples).issubset(set(product_tuples))}"
    )

    # Show summary
    print("\n" + "-" * 70)
    print("Step 5: Summary")
    print("-" * 70)
    pool.print_summary()


if __name__ == "__main__":
    demo()
