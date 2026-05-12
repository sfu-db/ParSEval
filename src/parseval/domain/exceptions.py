class DomainError(Exception):
    """Base exception for schema-only domain generation."""


class TypeCoercionError(DomainError):
    """Raised when a value cannot be coerced to the required column type."""


class ConstraintViolationError(DomainError):
    """Raised when a value violates a column constraint (NOT NULL, range, etc.)."""


class UniqueConflictError(ConstraintViolationError):
    """Raised when a UNIQUE constraint is violated by a duplicate value."""


class ForeignKeyResolutionError(ConstraintViolationError):
    """Raised when a foreign key reference cannot be resolved to a parent row."""


class ConstraintConflict(DomainError):
    """Raised when constraints are contradictory and cannot be satisfied."""
