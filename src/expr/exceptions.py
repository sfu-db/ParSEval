


class ExpressionError(Exception):
    """Base class for expression-related errors"""
    pass

class TypeMismatchError(ExpressionError):
    """Raised when expression types are incompatible"""
    pass
