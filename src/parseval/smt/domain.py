from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable, Optional, Set, TYPE_CHECKING, Tuple
from abc import ABC, abstractmethod
import random, time, logging
from collections import deque

import string, re
from datetime import datetime, timedelta
from ..dtype import DataType, DATATYPE

from src.parseval import symbol as sym
from src.parseval.helper import like_to_pattern

logger = logging.getLogger(__name__)


class InConsistency(Exception):
    def __init__(self, message: str, variables: str | None = None):
        super().__init__(message)
        self.variables = variables


class UnionFind:
    """
    Tracks columns linked by equality using union-find.
    Uses ColumnRef.quality_name as variables.
    """

    def __init__(self):
        self.parent: Dict[str, str] = {}
        self.clusters: Dict[str, Set[str]] = {}
        self.inequalities: Set[Tuple[str, str]] = set()
        self.conflicts = set()

    def _normalize_pair(self, a: str, b: str) -> Tuple[str, str]:
        """Return pair in sorted order for consistent storage."""
        return (a, b) if a < b else (b, a)

    def find(self, column: str) -> str:
        if column not in self.parent:
            self.parent[column] = column
            self.clusters[column] = {column}
        if self.parent[column] != column:
            self.parent[column] = self.find(self.parent[column])
        return self.parent[column]

    def union(self, a: str, b: str):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        # keep deterministic: smaller string as root
        cluster1 = self.clusters[ra]
        cluster2 = self.clusters[rb]

        for ca in cluster1:
            for cb in cluster2:
                pair = self._normalize_pair(ca, cb)
                if pair in self.inequalities:
                    self.conflicts.add(pair)
                    return False

        if len(cluster1) < len(cluster2):
            ra, rb = rb, ra
            cluster1, cluster2 = cluster2, cluster1

        cluster1 |= cluster2
        for c in cluster2:
            self.parent[c] = ra
        self.clusters[ra] = cluster1
        del self.clusters[rb]
        return True

    def add_inequality(self, a: str, b: str):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            pair = self._normalize_pair(a, b)
            self.conflicts.add(pair)
            return False
        pair = self._normalize_pair(a, b)
        self.inequalities.add(pair)
        return True

    def connected(self, a: str, b: str) -> bool:
        return self.find(a) == self.find(b)

    def validate(self) -> List[str]:
        errors = []
        # Check for conflicts
        if self.conflicts:
            for a, b in self.conflicts:
                errors.append(f"Conflict: {a} and {b} are both equal and unequal")

        # Verify inequalities don't exist within clusters
        for root, cluster in self.clusters.items():
            members = list(cluster)
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    pair = self._normalize_pair(a, b)
                    if pair in self.inequalities:
                        errors.append(
                            f"Inconsistency: {a} and {b} are in same cluster but marked unequal"
                        )
        return errors

    def groups(self) -> List[Set[str]]:
        roots: Dict[str, Set[str]] = {}
        for v in list(self.parent.keys()):
            r = self.find(v)
            roots.setdefault(r, set()).add(v)
        return list(roots.values())


@dataclass(frozen=True)
class DomainSpec:
    """
    This is a static specification of a logical column domain from database schema.
    """

    table_name: str
    column_name: str
    datatype: DATATYPE
    min_val: Any = None
    max_val: Any = None
    choices: List[Any] = field(default_factory=list)
    length: Optional[int] = None
    pattern: Optional[str] = None
    unique: bool = False
    nullable: bool = False
    default: Any = None
    checks: Optional[List[Callable[[Any], bool]]] = None
    # Shared mutable state — all ValuePools referring to this DomainSpec share these
    generated: List[Any] = field(default_factory=list)
    excluded: Set[Any] = field(default_factory=set)
    ############################################################################

    def __post_init__(self):
        object.__setattr__(self, "datatype", DataType.build(self.datatype))

    @property
    def qualified_name(self):
        return f"{self.table_name}.{self.column_name}"

    def __repr__(self):
        return f"Domain({self.qualified_name}, {self.datatype}, unique={self.unique})"


class ValuePool:
    """
    Holds a set of produced values for a logical domain.
    If 'unique' True, values are intended to be unique (PK).
    'locked' indicates pool is referenced by dependents (FK linking)
    so expansions are more controlled.
    """

    def __init__(self, alias: str, domain: DomainSpec, datatype=None):
        self.alias = alias
        self.domain = domain
        self.unique = domain.unique

        self.local_values: Set[Any] = set()  # values generated in this pool only
        self.local_excluded: Set[Any] = set()  # excluded values in this pool only

        self.choices: List[Any] = domain.choices.copy()
        self.min_val = domain.min_val
        self.max_val = domain.max_val
        self.pattern: Optional[str] = None
        self.length: Optional[int] = None
        self.datatype = datatype if datatype else domain.datatype
        self._locked = False
        self._cursor = None

    @property
    def datatype(self):
        return self._datatype

    @datatype.setter
    def datatype(self, value):
        self._datatype = DataType.build(value)

    def __hash__(self):
        return hash((self.alias, self.domain.qualified_name))

    # --- Behavior ---
    def add_local_value(self, v: Any):
        """Add a value visible only to this alias."""
        if v not in self.domain.excluded and v not in self.local_excluded:
            self.local_values.add(v)
            # if propagate:
            #     for lp in self.linked_pools:
            #         logging.info(f"propagating value {v} to pool {lp.alias}")
            #         lp.add_local_value(v, propagate=False)

    def get_domain_excluded(self) -> Set[Any]:
        return self.local_excluded | self.domain.excluded

    def get_domain_values(self) -> Set[Any]:
        return (
            self.local_values | set(self.domain.generated) - self.get_domain_excluded()
        )

    def expand_domain(self, additional_samples: int = 10):
        if self.domain.column_name == "OpenDate":
            logger.info(
                f"Expanding domain for {self.alias} ({self.domain.qualified_name}) with datatype {self.datatype}"
            )
        """Generate a new value for the pool based on domain spec."""
        if self.choices:
            for choice in self.choices:
                if (
                    choice not in self.local_excluded
                    and choice not in self.domain.excluded
                ):
                    if self.unique and (
                        choice in self.local_values or choice in self.domain.generated
                    ):
                        continue
                    self.add_local_value(choice)
            additional_samples -= len(self.choices)
            return

        datatype = self.datatype
        for _ in range(additional_samples):
            attempts = 0
            while attempts < 2000:
                attempts += 1
                if datatype.is_type(*DataType.NUMERIC_TYPES):
                    value = (
                        self._sample_int()
                        if datatype.is_type(*DataType.INTEGER_TYPES)
                        else self._sample_float()
                    )
                elif datatype.is_type(*DataType.TEXT_TYPES):
                    value = self._sample_str()
                elif datatype.is_type(DataType.Type.BOOLEAN):
                    value = self._sample_bool()
                elif datatype.is_type(*DataType.TEMPORAL_TYPES):
                    value = self._sample_date()
                else:
                    value = self._sample_int()

                if value in self.local_excluded or value in self.domain.excluded:
                    continue
                if self.unique and (
                    value in self.local_values or value in self.domain.generated
                ):
                    continue
                self.add_local_value(value)
                break

    def _sample_int(self):
        low = self.min_val
        high = self.max_val

        if low is None and high is None:
            low, high = 0, 1000
            descending = False
        elif low is None:
            # only upper bound known → generate downward
            low, high = high - 1000 if isinstance(high, int) else 0, high
            descending = True
        else:
            descending = False

        if high is None:
            high = low + 1000
            descending = False

        if self._cursor is None:
            self._cursor = high if descending else low
        else:
            self._cursor += -1 if descending else 1

        while self._cursor in self.local_excluded or self._cursor in self.local_values:
            self._cursor += -1 if descending else 1
        return self._cursor

    def _sample_float(self):
        return float(self._sample_int())

    def _sample_str(self):
        if self.choices:
            return random.choice(self.choices)
        alphabet = string.ascii_letters + string.digits + " "
        length = self.length or 10
        pattern = self.pattern or ("_" * length)
        # if not pattern:
        #     pattern = "_" * 10
        result = ""
        i = 0
        while i < len(pattern):
            c = pattern[i]
            if c == "%":
                # random or sequential filler
                length = random.randint(0, 3)
                filler = "".join(random.choice(alphabet) for _ in range(length))
                result += filler
            elif c == "_":
                result += random.choice(alphabet)
            else:
                result += c
            i += 1
        return result

    def _sample_bool(self):
        samples = set(self.choices) if self.choices else set(True, False)
        if self.domain.nullable:
            samples.add(None)
        return random.choice(samples)

    def _sample_date(self):
        low = self.min_val
        high = self.max_val
        step = timedelta(days=1)

        if low is None and high is None:
            high = datetime.now()
            low = high - timedelta(days=365 * 10)
            descending = False
        elif low is None:
            descending = True
            low = high - timedelta(days=365 * 10)
        elif high is None:
            descending = False
            high = low + timedelta(days=365 * 10)
        else:
            descending = False

        if self._cursor is None:
            self._cursor = high if descending else low
        else:
            self._cursor += -step if descending else step

        while self._cursor in self.local_excluded or self._cursor in self.local_values:
            self._cursor += -step if descending else step

        return self._cursor

    def _inconsistency_detected(self):
        if self.min_val is not None and self.max_val is not None:
            if self.min_val > self.max_val:
                raise InConsistency(
                    f"Inconsistent bounds for {self.alias}: "
                    f"min {self.min_val} > max {self.max_val}",
                    variables=[self.alias],
                )

    def apply_constraints(self, constraint: sym.Condition):
        op = constraint.key

        if self.datatype.is_type(*DataType.NUMERIC_TYPES):
            if op in {"gt", "ge"}:
                self.propagate_bounds(min_val=constraint.right.value)
            elif op in {"lt", "le"}:
                self.propagate_bounds(max_val=constraint.right.value)
            elif op in {"eq", "IS"}:
                self.local_values = {constraint.right.value}
                self.choices.append(constraint.right.value)
                self.min_val = self.max_val = constraint.right.value
            elif op in {"ne"}:
                self.add_excluded(constraint.right.value)
            else:
                raise NotImplementedError(f"Numeric op {op} not implemented")
        elif self.datatype.is_type(*DataType.TEMPORAL_TYPES):
            if op in {"gt", "ge"}:
                self.propagate_bounds(min_val=constraint.right.value)
            elif op in {"lt", "le"}:
                values = constraint.right.find_all(sym.Const)
                value = datetime.strptime(values[0].value, "%Y-%m-%d")
                self.propagate_bounds(max_val=value)
            elif op in {"=", "eq"}:
                self.local_values = {constraint.right.value}
                self.min_val = self.max_val = constraint.right.value
            elif op in {"!=", "ne"}:
                self.add_excluded(constraint.right.value)
            else:
                raise NotImplementedError(f"Datetime op {op} not implemented")
        elif self.datatype.is_type(DataType.Type.BOOLEAN):
            if op in {"eq", "=="}:
                self.local_values = {constraint.right.value}
            elif op in {"ne", "<>"}:
                self.add_excluded(constraint.right.value)
            else:
                raise NotImplementedError(f"Boolean op {op} not implemented")
        elif self.datatype.is_type(*DataType.TEXT_TYPES):
            if op in {"=", "eq", "IS"}:
                self.choices.append(constraint.right.value)
                self.local_values = {constraint.right.value}
            elif op in {"ne", "<>"}:
                self.add_excluded(constraint.right.value)
            elif op.upper() == "LIKE":
                raw_pattern = constraint.right.value
                regex = like_to_pattern(raw_pattern)
                self.local_values = {
                    v for v in self.local_values if regex.match(str(v))
                }
                self.choices = [v for v in self.choices if regex.match(str(v))]
                self.pattern = raw_pattern
            else:
                raise NotImplementedError(f"String op {op} not implemented")
        else:
            raise NotImplementedError

        self._inconsistency_detected()

    def add_excluded(self, v: Any):
        self.local_excluded.add(v)

    def propagate_bounds(self, min_val=None, max_val=None):
        if self.datatype.is_type(*DataType.NUMERIC_TYPES):
            if min_val is not None:
                self.min_val = max(self.min_val or min_val, min_val)
            if max_val is not None:
                self.max_val = min(self.max_val or max_val, max_val)
            self.local_values = {
                v
                for v in self.local_values
                if (self.min_val is None or v >= self.min_val)
                and (self.max_val is None or v <= self.max_val)
            }
        elif self.datatype.is_type(*DataType.TEMPORAL_TYPES):
            if min_val is not None:
                self.min_val = max(self.min_val, min_val) if self.min_val else min_val
            if max_val is not None:
                self.max_val = min(self.max_val, max_val) if self.max_val else max_val

            self.local_values = {
                v
                for v in self.local_values
                if (self.min_val is None or v >= self.min_val)
                and (self.max_val is None or v <= self.max_val)
            }
        elif min_val is not None or max_val is not None:
            raise ValueError(
                f"Cannot propagate bounds on non-numeric/datetime types. current type {self.datatype}"
            )

    def mark_locked(self):
        """Lock the pool when another column depends on it (e.g., FK reference)."""
        if not self._locked:
            self._locked = True
            logger.info(f"🔒 Pool locked with {len(self.values)} values")

    def __repr__(self):
        return (
            f"ValuePool(alias={self.alias}, domain={self.domain.qualified_name}, "
            f"local={len(self.local_values)}, shared={len(self.domain.generated)})"
        )


class ColumnDomainPool:
    def __init__(self):
        self._domains: Dict[str, DomainSpec] = {}
        self._pools: Dict[str, ValuePool] = {}
        self.union_find = UnionFind()

    def register_domain(self, domain: DomainSpec):
        key = domain.qualified_name
        if key not in self._domains:
            self._domains[key] = domain
        return self._domains[key]

    def add_equality(self, pool_a: ValuePool, pool_b: ValuePool):
        assert isinstance(pool_a, ValuePool) and isinstance(pool_b, ValuePool)

        alias = f"{pool_a.alias}|{pool_b.alias}"
        generate = pool_a.get_domain_values() | pool_b.get_domain_values()

        excluded = pool_a.get_domain_excluded() | pool_b.get_domain_excluded()

        domain = DomainSpec(
            table_name="eq",
            column_name=alias,
            datatype=pool_a.datatype,  # assuming same datatype
            unique=pool_a.unique or pool_b.unique,
            generated=list(generate),
            excluded=excluded,
        )

        merged = ValuePool(alias=alias, domain=domain)

        def min_bound(a, b):
            if a is None:
                return b
            if b is None:
                return a
            return max(a, b)  # tighter lower bound

        def max_bound(a, b):
            if a is None:
                return b
            if b is None:
                return a
            return min(a, b)  # tighter upper bound

        min_val = min_bound(pool_a.min_val, pool_b.min_val)
        max_val = max_bound(pool_a.max_val, pool_b.max_val)

        merged.propagate_bounds(min_val=min_val, max_val=max_val)

        conflicts = [
            self.union_find.union(alias, pool_a.alias),
            self.union_find.union(alias, pool_b.alias),
        ]
        if any(c is False for c in conflicts):
            return False
        self._pools[alias] = merged
        return True

    def add_inequality(self, pool_a: ValuePool, pool_b: ValuePool):
        assert isinstance(pool_a, ValuePool) and isinstance(pool_b, ValuePool)
        return self.union_find.add_inequality(pool_a.alias, pool_b.alias)

    ############################################################################

    def get_or_create_pool(
        self, alias: str, table_name: str, column_name: str
    ) -> Optional[ValuePool]:
        qualified_name = f"{table_name}.{column_name}"
        key = f"{alias}" if alias else qualified_name
        key = self.union_find.find(key)
        if key in self._pools:
            return self._pools[key]
        domain = self._domains.get(qualified_name)
        if not domain:
            raise KeyError(f"No DomainSpec registered for {qualified_name}")
        pool = ValuePool(alias, domain=domain)
        self._pools[key] = pool
        logger.debug(f"Created ValuePool for alias={alias}, domain={qualified_name}")
        return pool

    def get_pool(self, alias: str) -> Optional[ValuePool]:
        key = self.union_find.find(alias)
        return self._pools[key]

    def all_pools(self) -> List[ValuePool]:
        return list(self._pools.values())

    def expand_domain(self, alias: str, additional_samples: int = 10):
        """Expand a column's domain with more generated values."""
        pool = self.get_pool(alias)
        pool.expand_domain(additional_samples=additional_samples)

    def __repr__(self):
        return (
            f"<ColumnDomainPool {len(self._pools)} pools, {len(self._domains)} domains>"
        )
