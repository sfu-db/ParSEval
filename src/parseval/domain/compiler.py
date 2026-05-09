from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple

from parseval.dtype import DataType
from .constraints import (
    CheckConstraint,
    ContainsConstraint,
    ChoicesConstraint,
    LengthConstraint,
    ModuloConstraint,
    PatternConstraint,
    PrefixConstraint,
    RangeConstraint,
    SuffixConstraint,
)

from .spec import ColumnSpec
from sqlglot import exp

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple
from .exceptions import ConstraintConflict, ConstraintViolationError
import re

@dataclass(frozen=True)
class ColumnDomainPlan:
    """Normalized plan for generating and validating values for a single column."""
    nullable: bool = True
    unique: bool = False
    default: Any = None

    # Finite set of allowed values (e.g. from ENUM or ChoicesConstraint)
    allowed_values: Optional[Tuple[Any, ...]] = None
    
    # Values that must be excluded (e.g. NOT IN, or already used unique values)
    excluded_values: Tuple[Any, ...] = field(default_factory=tuple)

    # Range limits (Numeric, Temporal)
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    # Length limits (String, Bytes)
    minimum_length: Optional[int] = None
    maximum_length: Optional[int] = None
    
    # Simple pattern hints
    pattern: Optional[str] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    contains: Tuple[str, ...] = field(default_factory=tuple)
    modulo_divisor: Optional[int] = None
    modulo_remainder: int = 0

    # Opaque predicates that must be checked after generation (CheckConstraint lambdas)
    residual_predicates: Tuple[Callable[[Any], bool], ...] = field(default_factory=tuple)


class ConstraintCompiler:
    """Compiles ColumnSpec constraints into a normalized ColumnDomainPlan."""

    def compile(self, spec: ColumnSpec) -> ColumnDomainPlan:
        # Initial values from spec
        nullable = spec.nullable
        unique = spec.unique or spec.primary_key
        default = spec.default

        allowed_values: Optional[Tuple[Any, ...]] = None
        minimum: Optional[Any] = None
        maximum: Optional[Any] = None
        minimum_inclusive = True
        maximum_inclusive = True
        minimum_length: Optional[int] = None
        
        # Intersect spec.length and spec.datatype.length
        maximum_length: Optional[int] = spec.length
        
        def _to_int(val: Any) -> int:
            if isinstance(val, exp.Literal):
                return int(val.this)
            if hasattr(val, "this"):
                return _to_int(val.this)
            return int(val)

        def _extract_length(dt: Any) -> Optional[int]:
            # Try patched .length property
            l = getattr(dt, "length", None)
            if l is not None:
                try:
                    return _to_int(l)
                except (ValueError, TypeError):
                    pass
            
            # Fallback to expressions (sqlglot default for VARCHAR(N))
            # But only for types that usually have length and if the first param is a number
            if dt.args.get("expressions") and not dt.is_type(DataType.Type.ENUM):
                try:
                    return _to_int(dt.args["expressions"][0])
                except (ValueError, TypeError):
                    pass
            return None

        dtype_length = _extract_length(spec.datatype)
        if dtype_length is not None:
            dl = dtype_length
            if maximum_length is None or dl < maximum_length:
                maximum_length = dl

        pattern: Optional[str] = None
        prefix: Optional[str] = None
        suffix: Optional[str] = None
        contains: List[str] = []
        modulo_divisor: Optional[int] = None
        modulo_remainder = 0
        residual_predicates = []

        # 1. Handle Datatype-derived constraints
        if spec.datatype.is_type(DataType.Type.ENUM):
            
            enum_values = []
            for e in spec.datatype.args.get("expressions", []):
                val = e.this if isinstance(e, exp.Literal) else str(e)
                if val not in enum_values:
                    enum_values.append(val)
            
            if allowed_values is None:
                allowed_values = tuple(enum_values)
            else:
                allowed_values = self._intersect_preserving_order(
                    allowed_values, enum_values
                )

        # 2. Iterate through checks
        for check in spec.checks:
            if isinstance(check, ChoicesConstraint):
                check_values = tuple(check.values)
                if allowed_values is None:
                    allowed_values = check_values
                else:
                    allowed_values = self._intersect_preserving_order(
                        allowed_values, check_values
                    )
                
                if not allowed_values:
                    raise ConstraintConflict(f"Empty intersection of allowed values for {spec.qualified_name}")

            elif isinstance(check, RangeConstraint):
                # Intersect Range
                if check.minimum is not None:
                    if minimum is None or check.minimum > minimum:
                        minimum = check.minimum
                        minimum_inclusive = check.minimum_inclusive
                    elif check.minimum == minimum:
                        minimum_inclusive = minimum_inclusive and check.minimum_inclusive

                if check.maximum is not None:
                    if maximum is None or check.maximum < maximum:
                        maximum = check.maximum
                        maximum_inclusive = check.maximum_inclusive
                    elif check.maximum == maximum:
                        maximum_inclusive = maximum_inclusive and check.maximum_inclusive
                
                # Validation of Range
                if minimum is not None and maximum is not None:
                    if minimum > maximum:
                        raise ConstraintConflict(f"Contradictory range for {spec.qualified_name}: [{minimum}, {maximum}]")
                    if minimum == maximum and not (minimum_inclusive and maximum_inclusive):
                         raise ConstraintConflict(f"Contradictory range for {spec.qualified_name}: empty interval at {minimum}")

            elif isinstance(check, LengthConstraint):
                if check.minimum is not None:
                    if minimum_length is None or check.minimum > minimum_length:
                        minimum_length = check.minimum
                if check.maximum is not None:
                    if maximum_length is None or check.maximum < maximum_length:
                        maximum_length = check.maximum
                
                if minimum_length is not None and maximum_length is not None and minimum_length > maximum_length:
                    raise ConstraintConflict(f"Contradictory length for {spec.qualified_name}: [{minimum_length}, {maximum_length}]")

            elif isinstance(check, PatternConstraint):
                if pattern is not None and pattern != check.pattern:
                    # For now, we only support one pattern. Intersecting regex is hard.
                    # We might want to keep multiple or fail if they differ.
                    # The plan says: "unsupported forms stay as residual predicates or explicit unsupported errors"
                    residual_predicates.append(lambda x, p=check.pattern: self._check_pattern(x, p))
                else:
                    pattern = check.pattern

            elif isinstance(check, PrefixConstraint):
                if prefix is None or check.prefix.startswith(prefix):
                    prefix = check.prefix
                elif prefix.startswith(check.prefix):
                    pass
                else:
                    residual_predicates.append(
                        lambda x, p=check.prefix: str(x).startswith(p)
                    )

            elif isinstance(check, SuffixConstraint):
                if suffix is None or check.suffix.endswith(suffix):
                    suffix = check.suffix
                elif suffix.endswith(check.suffix):
                    pass
                else:
                    residual_predicates.append(
                        lambda x, s=check.suffix: str(x).endswith(s)
                    )

            elif isinstance(check, ContainsConstraint):
                if check.substring not in contains:
                    contains.append(check.substring)

            elif isinstance(check, ModuloConstraint):
                if modulo_divisor is None:
                    modulo_divisor = check.divisor
                    modulo_remainder = check.remainder % check.divisor
                elif modulo_divisor == check.divisor:
                    if modulo_remainder != (check.remainder % check.divisor):
                        raise ConstraintConflict(
                            f"Contradictory modulo constraint for {spec.qualified_name}"
                        )
                else:
                    residual_predicates.append(
                        lambda x, d=check.divisor, r=check.remainder: x % d == r
                    )

            elif isinstance(check, CheckConstraint):
                if callable(check.expression):
                    residual_predicates.append(check.expression)

        return ColumnDomainPlan(
            nullable=nullable,
            unique=unique,
            default=default,
            allowed_values=allowed_values,
            minimum=minimum,
            maximum=maximum,
            minimum_inclusive=minimum_inclusive,
            maximum_inclusive=maximum_inclusive,
            minimum_length=minimum_length,
            maximum_length=maximum_length,
            pattern=pattern,
            prefix=prefix,
            suffix=suffix,
            contains=tuple(contains),
            modulo_divisor=modulo_divisor,
            modulo_remainder=modulo_remainder,
            residual_predicates=tuple(residual_predicates),
        )

    def _check_pattern(self, value: Any, pattern: str) -> bool:
        import re
        if value is None:
            return True
        return bool(re.search(pattern, str(value)))

    def _intersect_preserving_order(
        self, current: Iterable[Any], incoming: Iterable[Any]
    ) -> Tuple[Any, ...]:
        incoming_values = tuple(incoming)
        incoming_set = set(incoming_values)
        return tuple(value for value in current if value in incoming_set)



class ConstraintValidator:
    """Validates values against a compiled ColumnDomainPlan."""

    def validate(self, plan: ColumnDomainPlan, value: Any, column_name: str = "column") -> None:
        """
        Validates the value against the plan.
        Raises ConstraintViolationError if invalid.
        """
        if value is None:
            if not plan.nullable:
                raise ConstraintViolationError(f"{column_name} does not allow NULL")
            return

        # 1. Allowed values
        if plan.allowed_values is not None:
            if value not in plan.allowed_values:
                raise ConstraintViolationError(
                    f"Value {value!r} for {column_name} is not in allowed values: {plan.allowed_values}"
                )

        # 2. Excluded values
        if value in plan.excluded_values:
            raise ConstraintViolationError(
                f"Value {value!r} for {column_name} is in excluded values"
            )

        # 3. Range
        if plan.minimum is not None:
            if plan.minimum_inclusive:
                if value < plan.minimum:
                    raise ConstraintViolationError(f"Value {value!r} for {column_name} is below minimum {plan.minimum}")
            else:
                if value <= plan.minimum:
                    raise ConstraintViolationError(f"Value {value!r} for {column_name} must be strictly greater than {plan.minimum}")

        if plan.maximum is not None:
            if plan.maximum_inclusive:
                if value > plan.maximum:
                    raise ConstraintViolationError(f"Value {value!r} for {column_name} is above maximum {plan.maximum}")
            else:
                if value >= plan.maximum:
                    raise ConstraintViolationError(f"Value {value!r} for {column_name} must be strictly less than {plan.maximum}")

        # 4. Length
        if plan.minimum_length is not None or plan.maximum_length is not None:
            try:
                length = len(value)
                if plan.minimum_length is not None and length < plan.minimum_length:
                    raise ConstraintViolationError(f"Value length {length} for {column_name} is below minimum length {plan.minimum_length}")
                if plan.maximum_length is not None and length > plan.maximum_length:
                    raise ConstraintViolationError(f"Value length {length} for {column_name} is above maximum length {plan.maximum_length}")
            except TypeError:
                # Value doesn't support len()
                pass

        # 5. Pattern
        if plan.pattern is not None:
            if not re.search(plan.pattern, str(value)):
                raise ConstraintViolationError(f"Value {value!r} for {column_name} does not match pattern {plan.pattern}")

        if plan.prefix is not None and not str(value).startswith(plan.prefix):
            raise ConstraintViolationError(f"Value {value!r} for {column_name} does not start with {plan.prefix!r}")

        if plan.suffix is not None and not str(value).endswith(plan.suffix):
            raise ConstraintViolationError(f"Value {value!r} for {column_name} does not end with {plan.suffix!r}")

        for substring in plan.contains:
            if substring not in str(value):
                raise ConstraintViolationError(
                    f"Value {value!r} for {column_name} does not contain {substring!r}"
                )

        if plan.modulo_divisor is not None:
            if value % plan.modulo_divisor != plan.modulo_remainder:
                raise ConstraintViolationError(
                    f"Value {value!r} for {column_name} does not satisfy modulo constraint"
                )

        # 6. Residual predicates
        for predicate in plan.residual_predicates:
            if not predicate(value):
                raise ConstraintViolationError(f"Value {value!r} for {column_name} failed a check constraint")

    def is_valid(self, plan: ColumnDomainPlan, value: Any) -> bool:
        """Returns True if the value is valid according to the plan, False otherwise."""
        try:
            self.validate(plan, value)
            return True
        except ConstraintViolationError:
            return False
