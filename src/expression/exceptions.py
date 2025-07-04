


class ExpressionError(Exception):
    """Base class for expression-related errors"""
    pass

class TypeMismatchError(ExpressionError):
    """Raised when expression types are incompatible"""
    pass


class QueryValidationError(Exception):
    """Raised when query validation fails"""
    pass

class SchemaValidationError(Exception):
    """Raised when schema validation fails"""
    pass