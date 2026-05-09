class DomainError(Exception):
    """Base exception for schema-only domain generation."""


class TypeCoercionError(DomainError):
    pass


class ConstraintViolationError(DomainError):
    pass


class UniqueConflictError(ConstraintViolationError):
    pass


class ForeignKeyResolutionError(ConstraintViolationError):
    pass


class ConstraintConflict(DomainError):
    """Raised when constraints are contradictory and cannot be satisfied."""
