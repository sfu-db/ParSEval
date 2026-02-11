from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable, Optional, Set, TYPE_CHECKING, Tuple, Generic, TypeVar, Type
from abc import ABC, abstractmethod
import random, time, logging, threading
from collections import deque
from sqlglot.schema import normalize_name
import string, re
from datetime import datetime, timedelta
from sqlglot import exp
from src.parseval.dtype import DataType
from src.parseval.plan.rex import Is_Null
from src.parseval.helper import like_to_pattern
logger = logging.getLogger(__name__)

    
T = TypeVar('T')
ValueType = TypeVar('ValueType')

class RelationshipType(Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    # MANY_TO_ONE = "many_to_one"
    # MANY_TO_MANY = "many_to_many"

class DependencyType(Enum):
    FOREIGN_KEY = "foreign_key"
    INNER_JOIN = "inner_join"
    LEFT_JOIN = "left_join"
    RIGHT_JOIN = "right_join"
    FULL_JOIN = "full_join"
    NATURAL_JOIN = "natural_join"



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

    def add_conflict(self, a: str, b: str):
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
    def clear(self):
        self.parent.clear()
        self.clusters.clear()
        self.inequalities.clear()
        self.conflicts.clear()
        
@dataclass(frozen=True)
class DomainSpec:
    """
    This is a static specification of a logical column domain from database schema.
    """
    column_domains: ColumnDomainPool
    table: exp.Table
    column: exp.Column
    datatype: exp.DATA_TYPE
    constraints: List[exp.Expression] = field(default_factory=list)
    unique: bool = False
    nullable: bool = False
    generated: Set[Any] = field(default_factory=set)
    excluded: Set[Any] = field(default_factory=set)
    dependencies: Set['DomainSpec'] = field(default_factory=set)
    dependents: Set['DomainSpec'] = field(default_factory=set)
    reuse_rate: float = 0.5  # fraction of reused values when generating
    unique_rate: float = 1.0  # fraction of unique values when generating
    

    def __post_init__(self):
        object.__setattr__(self, "datatype", DataType.build(self.datatype))
    
    def add_dependency(self, domain: 'DomainSpec') -> None:
        """Add a dependency to this domain"""
        self.dependencies.add(domain)
        domain.dependents.add(self)

    @property
    def qualified_name(self):
        return f"{self.table}.{self.column}"

    def __repr__(self):
        return f"Domain({self.qualified_name}, {self.datatype}, unique={self.unique}, nullable={self.nullable})"

class ValuePool(Generic[ValueType]):
    """
    Holds a set of produced values for a logical domain.
    If 'unique' True, values are intended to be unique (PK).
    'locked' indicates pool is referenced by dependents (FK linking)
    so expansions are more controlled.
    """

    def __init__(self, alias: str, domain: DomainSpec, datatype=None):
        self.alias = alias
        self.domain = domain
        
        self._constraints: List[exp.Expression] = list(domain.constraints)
        
        self._generated_values: Set[ValueType] = set()  # values generated in this pool only
        self._available_values: Set[ValueType] = set()  # values available for sampling
        self._excluded_values: Set[ValueType] = set()  # excluded values in this pool only
        
        self._dependencies: Set['ValuePool'] = set()
        self._dependents: Set['ValuePool'] = set()
        self.relationships: Dict['ValuePool', RelationshipType] = {}
        
        # Statistics
        self._generation_count = 0
        self._reuse_count = 0
        self._cache_size = 1000
        self.max_reuse_rate = 0.5  # max fraction of reused values
        self._cached_values: Dict[Any, int] = {}
        
        # Lock for thread safety
        self._lock = threading.RLock()
        self.datatype = datatype if datatype else domain.datatype
        self._locked = False
        self._cursor = None
    @property
    def constraints(self):
        return self.domain.constraints + self._constraints
    
    @property
    def unique_values_count(self):
        return len(set(self._generated_values) | set(self.domain.generated))
    
    @property
    def reuse_rate(self):
        total = self._generation_count + self._reuse_count
        return self._reuse_count / total if total > 0 else 0.0
    @property
    def unique_rate(self):
        total = len(self._generated_values ) + len( self.domain.generated)
        if total == 0:
            return 1.0
        return self.unique_values_count / total
    
    @property
    def unique(self):
        return self.domain.unique
    
    @property
    def nullable(self):    
        return self.domain.nullable
    
    def _record_generation(self, value: Any, is_new):
        if is_new:
            self._generation_count += 1
            if len(self._available_values) < self._cache_size:
                self._available_values.add(value)
        else:
            self._reuse_count += 1
        self._cached_values[value] = self._cached_values.get(value, 0) + 1
    
    def _should_generate_null(self, max_null_rate: float = 0.1) -> bool:
        if not self.nullable or self._constraints:
            return False
        if self._constraints and any(isinstance(c, Is_Null) for c in self._constraints):
            return True
        elif self._constraints:
            return False
        total = self._generation_count + self._reuse_count + len(self.domain.generated) 
        if total == 0:
            return False
        values = self._generated_values | self.domain.generated
        current_nulls = sum(1 for v in values if v is None)
        none_ratio = current_nulls / total
        probability = 0.3 * (1 / (1 + 2 * none_ratio))
        return none_ratio < max_null_rate and random.random() < probability #(max_null_rate - current_null_rate) # probability #
    
    def _should_generate_new(self, max_reuse_rate: float = 0.3,
                           min_uniqueness_rate: float = 0.7) -> bool:
        if self.unique:
            return True
        if not self.available_values:
            return True
        
        
        total = self._generation_count + self._reuse_count + len(self.domain.generated)
        
        if total == 0:
            return True
        if self.reuse_rate < max_reuse_rate and self.available_values:
            return False
        if self.unique_rate < min_uniqueness_rate:
            return True
        return True
    def _get_most_reused_values(self, top_n: int = 5) -> List[Tuple[Any, int]]:
        """获取最常复用的值"""
        sorted_values = sorted(
            self._cached_values.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_values[:top_n]


    
    @property
    def datatype(self):
        return self._datatype

    @datatype.setter
    def datatype(self, value):
        self._datatype = DataType.build(value)

    def add_dependency(self, pool: 'ValuePool', relationship: DependencyType | str) -> None:
        """Add a dependency to this pool"""
        self._dependencies.add(pool)
        pool._dependents.add(self)
        relationship = DependencyType(relationship) if isinstance(relationship, str) else relationship
        self.relationships[pool] = relationship
    def propagate_constraint(self, constraint: exp.Expression) -> None:
        self._constraints.append(constraint)
        
    def __hash__(self):
        return hash((self.alias, self.domain.qualified_name))
    @property
    def available_values(self) -> List[ValueType]:
        return list(self.domain.generated | self._available_values)
        
    def add_generated_value(self, v: Any):
        """Add a value visible only to this alias."""
        if v not in self.domain.excluded and v not in self._excluded_values:
            self._generated_values.add(v)
    
    def generate(self, max_attempts: int = 100) -> ValueType:
        for _ in range(max_attempts):
            if self._should_generate_null():
                self._record_generation(value=None, is_new=True)
                return None
            elif self._should_generate_new(self.max_reuse_rate, self.domain.unique_rate) or not self._available_values:
                skips = self.domain.excluded.union(self._excluded_values)
                if self.unique:
                    skips = skips.union(self._generated_values).union(self.domain.generated)
                new_values = self._generate(1, skips= skips)
                for new_value in new_values:
                    if new_value not in self._generated_values:
                        self._generated_values.add(new_value)
                        self._record_generation(new_value, True)
                        return new_value
            else:
                value = self._select_value_for_reuse()
                self._record_generation(value=value, is_new=False)
                return value
        raise RuntimeError(f"Failed to generate a valid value for domain {self.domain} after {max_attempts} attempts.")
                
    def _generate(self, count: int = 1, skips: Optional[Set[Any]] = None) -> List[ValueType]:
        generator = ValueGeneratorFactory.create(self)
        return [generator.generate(skips) for _ in range(count)]

    def _select_value_for_reuse(self) -> ValueType:
        value = self.available_values.pop()
        return value
    def exclude(self, v: Any):
        """Exclude a value from being generated in this pool."""        
        self._excluded_values.add(v)
    
    def __repr__(self):
        return f"ValuePool({self.alias}, {self.domain}, unique={self.unique})"
        
class ValueGenerator(ABC, Generic[ValueType]):
    def __init__(self, pool: ValuePool):
        super().__init__()
        self.pool = pool
        self._cache = {}
    
    @abstractmethod
    def generate(self, skips: Optional[Set[Any]] = None) -> ValueType:
        pass
    
    @abstractmethod
    def validate(self, value: ValueType, skips: Optional[Set[Any]] = None ) -> bool:
        pass
    def generate_batch(self, count: int, skips: Optional[Set[Any]] = None) -> List[ValueType]:
        return [self.generate(skips) for _ in range(count)]
    
class IntGenerator(ValueGenerator[int]):
    
    def propagate_constraints(self):
        self.min_value = 0
        self.max_value = 400
        self.fixed_value = None
        for constraint in self.pool.constraints:
            if isinstance(constraint, exp.Check):
                expr = constraint.this
                if isinstance(expr, exp.Between):
                    low = expr.args.get("low")
                    high = expr.args.get("high")
                    if isinstance(low, exp.Literal) and isinstance(high, exp.Literal):
                        self.min_value = int(low.name)
                        self.max_value = int(high.name)
            elif isinstance(constraint, exp.Predicate):
                left = constraint.args.get("this")
                right = constraint.args.get("expression")
                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    margin = 0 if isinstance(constraint, exp.EQ, exp.GTE, exp.LTE) else 1
                    if isinstance(constraint, exp.GT):
                        self.min_value = int(right.name) + margin
                    elif isinstance(constraint, exp.LT):
                        self.max_value = int(right.name) - margin
                    elif isinstance(constraint, exp.GTE):
                        self.min_value = int(right.name) + margin
                    elif isinstance(constraint, exp.LTE):
                        self.max_value = int(right.name) - margin
                    elif isinstance(constraint, exp.EQ):
                        self.fixed_value = int(right.name)
                        
    def generate(self, skips = None):        
        self.propagate_constraints()
        if self.fixed_value is not None:
            if self.validate(self.fixed_value) and (skips is None or self.fixed_value not in skips):
                return self.fixed_value
        
        while True:
            value = random.randint(self.min_value, self.max_value)
            if self.validate(value, skips):
                return value
    
    def validate(self, value: int, skips) -> bool:
        if skips:
            return value not in skips
        return True
    
class StringGenerator(ValueGenerator[str]):
    def propagate_constraints(self):
        self.length = None
        self.pattern = None
        self.fixed_value = None
        for constraint in self.pool.constraints:
            if isinstance(constraint, exp.Check):
                expr = constraint.this
                if isinstance(expr, exp.Like):
                    pattern_expr = expr.args.get("expression")
                    if isinstance(pattern_expr, exp.Literal):
                        like_pattern = pattern_expr.name
                        self.pattern = like_to_pattern(like_pattern)
            elif isinstance(constraint, exp.Like):
                raw_pattern = constraint.right.value
                regex = like_to_pattern(raw_pattern)
                self.pattern = regex                
            elif isinstance(constraint, (exp.EQ, exp.Is)):
                left = constraint.left
                right = constraint.right
                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    self.fixed_value = right.name
            elif isinstance(constraint, exp.Predicate):
                if isinstance(constraint.left, exp.Length):
                    length_expr = constraint.left
                    arg = length_expr.args.get("this")
                    if isinstance(arg, exp.Column):
                        right = constraint.right
                        if isinstance(right, exp.Literal):
                            length_value = int(right.name)
                        elif isinstance(right, exp.Column):
                            length_value = right.datatype.length
                        else:
                            continue
                        if isinstance(constraint, (exp.EQ, exp.GTE, exp.LTE)):
                            self.length = length_value
                        elif isinstance(constraint, exp.LT):
                            self.length = length_value - 1
                        elif isinstance(constraint, exp.GT):
                            self.length = length_value + 1
                            
                        
    def generate(self, skips) -> str:
        self.propagate_constraints()
        alphabet = string.ascii_letters + string.digits + " "
        result = ""
        while True:
            if self.fixed_value is not None and self.validate(self.fixed_value):
                return self.fixed_value
            length = self.length if self.length is not None else random.randint(5, 15)
            pattern = self.pattern or ("_" * length)
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
            if self.validate(result, skips):
                return result
            
    
    def validate(self, value, skips = None):
        if skips:
            return value not in skips
        return True
    
class DateGenerator(ValueGenerator[datetime]):
    def propagate_constraints(self):
        self.start_date = datetime(2000, 1, 1)
        self.end_date = datetime(2020, 12, 31)
        self.fixed_value = None
        for constraint in self.pool.constraints:
            if isinstance(constraint, exp.Check):
                expr = constraint.this
                if isinstance(expr, exp.Between):
                    low = expr.args.get("low")
                    high = expr.args.get("high")
                    if isinstance(low, exp.Literal):
                        self.start_date = datetime.fromisoformat(low.name)
                    if isinstance(high, exp.Literal):
                        self.end_date = datetime.fromisoformat(high.name)
                    
            elif isinstance(constraint, exp.Predicate):
                left = constraint.args.get("this")
                right = constraint.args.get("expression")
                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    date_value = datetime.fromisoformat(right.name)
                    margin = timedelta(days=0) if isinstance(constraint, (exp.EQ, exp.GTE, exp.LTE)) else timedelta(days=1)
                    if isinstance(constraint, exp.GT):
                        self.start_date = max(self.start_date, date_value + margin)
                    elif isinstance(constraint, exp.LT):
                        self.end_date = min(self.end_date, date_value - margin)
                    elif isinstance(constraint, exp.GTE):
                        self.start_date = max(self.start_date, date_value + margin)
                    elif isinstance(constraint, exp.LTE):
                        self.end_date = min(self.end_date, date_value - margin)
                    elif isinstance(constraint, exp.EQ):
                        self.fixed_value = date_value
        
    def generate(self, skips) -> datetime:
        self.propagate_constraints()
        if self.fixed_value is not None:
            if self.validate(self.fixed_value):
                return self.fixed_value
        while True:
            start_date = self.start_date if self.start_date is not None else datetime(1970, 1, 1)
            end_date = self.end_date if self.end_date is not None else datetime(2026, 12, 31)
            delta = end_date - start_date
            random_days = random.randint(0, delta.days)
            value = start_date + timedelta(days=random_days)
            if self.validate(value, skips):
                return value
    
    def validate(self, value: datetime, skips = None) -> bool:
        if skips:
            return value not in skips
        return True
    
class DecimalGenerator(ValueGenerator[float]):
    def propagate_constraints(self):
        self.min_value = 0.0
        self.max_value = 10000.0
        self.fixed_value = None
        for constraint in self.pool.constraints:
            if isinstance(constraint, exp.Check):
                expr = constraint.this
                if isinstance(expr, exp.Between):
                    low = expr.args.get("low")
                    high = expr.args.get("high")
                    if isinstance(low, exp.Literal):
                        self.min_value = float(low.name)
                    if isinstance(high, exp.Literal):
                        self.max_value = float(high.name)
            elif isinstance(constraint, exp.Predicate):
                left = constraint.args.get("this")
                right = constraint.args.get("expression")
                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    margin = 0.0 if isinstance(constraint, (exp.EQ, exp.GTE, exp.LTE)) else 0.01
                    if isinstance(constraint, exp.GT):
                        self.min_value = float(right.name) + margin
                    elif isinstance(constraint, exp.LT):
                        self.max_value = float(right.name) - margin
                    elif isinstance(constraint, exp.GTE):
                        self.min_value = float(right.name) + margin
                    elif isinstance(constraint, exp.LTE):
                        self.max_value = float(right.name) - margin
                    elif isinstance(constraint, exp.EQ):
                        self.fixed_value = float(right.name)
    def generate(self, skips) -> float:
        self.propagate_constraints()
        if self.fixed_value is not None:
            if self.validate(self.fixed_value) and (skips is None or self.fixed_value not in skips):
                return self.fixed_value
        
        while True:
            value = random.randint(self.min_value, self.max_value)
            if self.validate(value, skips):
                return value
    
    def validate(self, value: float, skips = None) -> bool:
        if skips:
            return value not in skips
        return True
    
class BooleanGenerator(ValueGenerator[bool]):
    def propagate_constraints(self):
        self.fixed_value = None
        for constraint in self.pool.constraints:
            if isinstance(constraint, exp.Predicate):
                left = constraint.args.get("this")
                right = constraint.args.get("expression")
                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    if isinstance(constraint, exp.EQ, exp.Is):
                        if right.name.lower() in ("true", "1"):
                            self.fixed_value = True
                        elif right.name.lower() in ("false", "0"):
                            self.fixed_value = False
    def generate(self, skips) -> bool:
        self.propagate_constraints()
        if self.fixed_value is not None:
            if self.validate(self.fixed_value) and (skips is None or self.fixed_value not in skips):
                return self.fixed_value
        while True:
            value = random.choice([True, False])
            if self.validate(value, skips):
                return value
    
    def validate(self, value: bool, skips) -> bool:
        return True
    
class ValueGeneratorFactory:
    _generators: Dict[DataType, Type[ValueGenerator]] = {
        **{DataType.build(dt): IntGenerator for dt in DataType.INTEGER_TYPES},
        **{DataType.build(dt): DecimalGenerator for dt in DataType.REAL_TYPES},
        **{DataType.build(dt): StringGenerator for dt in DataType.TEXT_TYPES},
        **{DataType.build(dt): DateGenerator for dt in DataType.TEMPORAL_TYPES},
        DataType.Type.BOOLEAN: BooleanGenerator
    }
    _semantic_generators: Dict[str, Type[ValueGenerator]] = {}
    
    @classmethod
    def register(cls, datatype: DataType, generator_cls: Type[ValueGenerator], semantic: Optional[str] = None):
        cls._generators[datatype] = generator_cls
        if semantic:
            cls._semantic_generators[semantic] = generator_cls
        
    @classmethod
    def create(cls, valuepool: ValuePool) -> ValueGenerator:
        if valuepool.domain.qualified_name in cls._semantic_generators:
            generator_cls = cls._semantic_generators[valuepool.domain.qualified_name]
            return generator_cls(valuepool)
        
        datatype = valuepool.datatype
        # if datatype.is_type(DataType.INTEGER_TYPES):
        generator_cls = cls._generators.get(datatype)
        if not generator_cls:
            raise ValueError(f"No generator registered for datatype {datatype}")
        return generator_cls(valuepool)
    
    @classmethod
    def get_available_generators(cls) -> List[DataType]:
        """Get list of supported datatypes"""
        return list(cls._generators.keys())



class ColumnDomainPool:
    def __init__(self, reuse_rate = 0.5, unique_rate = 1.0, seed = 142):
        self._domains: Dict[str, DomainSpec] = {}
        self._pools: Dict[str, ValuePool] = {}
        self.reuse_rate = reuse_rate
        self.unique_rate = unique_rate
        self.union_find = UnionFind()
        random.seed(seed)

    def get_domain(self, table: exp.Table | str, column: exp.Column | str) -> Optional[DomainSpec]:
        key = f"{table}.{column}"
        return self._domains[key]
    
    def register_domain(self, table: exp.Table | str, column: exp.Column | str, datatype: exp.DATA_TYPE,
                        constraints: Optional[List[exp.Expression]] = None, unique: bool = False,
                        nullable: bool = False, generated: Optional[Set[Any]] = None,
                        excluded: Optional[Set[Any]] = None) -> DomainSpec:
        table = exp.maybe_parse(table, into= exp.Table)
        column = column if isinstance(column, exp.Column) else exp.Column(this= column)
        key = f"{table}.{column}"
        if key not in self._domains:
            spec = DomainSpec(self, table, column, datatype, constraints= constraints or [], unique= unique, nullable= nullable, generated= generated or set(), excluded= excluded or set(), unique_rate= self.unique_rate, reuse_rate= self.reuse_rate)
            self._domains[key] = spec
        return self._domains[key]
    
    def get_or_create_pool(
        self, table: exp.Table | str, column: exp.Column | str, alias: Optional[str] = None
    ) -> Optional[ValuePool]:
        if alias is None:
            alias = f"{table}.{column}"
        if alias in self._pools:
            return self._pools[alias]
        table = normalize_name(table, is_table=True).name
        column = normalize_name(column).name
        qualified_name = f"{table}.{column}"
        domain = self._domains.get(qualified_name)
        if not domain:
            raise KeyError(f"No DomainSpec registered for {qualified_name}")
        pool = ValuePool(alias, domain=domain)
        self._pools[alias] = pool
        logger.debug(f"Created ValuePool for alias={alias}, domain={qualified_name}")
        return pool
    
    def add_dependency(self, domain_a: str, domain_b: str):
        domain_a_spec = self._domains.get(domain_a)
        domain_b_spec = self._domains.get(domain_b)
        assert domain_a_spec is not None and domain_b_spec is not None
        domain_a_spec.add_dependency(domain_b_spec)
    
    def add_valuepool_dependence(self, pool_a: ValuePool, pool_b: ValuePool, relationship: DependencyType | str):
        assert isinstance(pool_a, ValuePool) and isinstance(pool_b, ValuePool)
        pool_a.add_dependency(pool_b, relationship)
    
    def add_valuepool_conflict(self, pool_a: ValuePool, pool_b: ValuePool):
        assert isinstance(pool_a, ValuePool) and isinstance(pool_b, ValuePool)
        return self.union_find.add_conflict(pool_a.alias, pool_b.alias)
    

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

    def get_pool(self, alias: str) -> Optional[ValuePool]:
        key = self.union_find.find(alias)
        return self._pools[key]

    def all_pools(self) -> List[ValuePool]:
        return list(self._pools.values())

    def expand_domain(self, alias: str, additional_samples: int = 10, max_attempts: int = 100):
        """Expand a column's domain with more generated values."""
        pool = self.get_pool(alias)
        for i in range(additional_samples):
            value = pool.generate(max_attempts= max_attempts)
            pool.add_generated_value(value)

    def __repr__(self):
        return (
            f"<ColumnDomainPool {len(self._pools)} pools, {len(self._domains)} domains>"
        )

