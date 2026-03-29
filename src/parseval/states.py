from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Set,
    Union,
    Optional,
    Generic,
    TypeVar,
    NamedTuple,
    Iterable,
    Type,
    Callable,
    Tuple,
)
import functools, logging

logger = logging.getLogger("parseval.uexpr")


class ParSEvalState(Enum):
    INITIAL = "initial"
    PARSING = "parsing"
    VALIDATING = "validating"
    TRANSFORMING = "transforming"
    COMPLETED = "completed"
    ERROR = "error"


T = TypeVar("T")  # Type for the success value
E = TypeVar("E")  # Type for the error value


class ParSEvalError(Exception):
    """Base exception for ParSEval-related errors.

    This exception is raised when there are general errors related to
    the ParSEval process.
    """

    pass


class SchemaException(ParSEvalError):
    """Base exception for schema-related errors.

    This exception is raised when there are issues related to the schema,
    such as missing columns, mismatched data types, or invalid schema definitions.
    """

    pass


class SyntaxException(ParSEvalError):
    """Base exception for syntax-related errors.

    This exception is raised when there are syntax errors in the input,
    such as invalid query syntax or malformed expressions.
    """

    pass


class ValidationException(ParSEvalError):
    """Base exception for validation-related errors.

    This exception is raised when validation checks fail, such as
    constraints on data integrity, uniqueness, or nullability.
    """

    pass


from sqlglot.errors import ParseError, SchemaError, OptimizeError, UnsupportedError

ExceptionTypes = Tuple[Type[BaseException], ...]


def raise_exception(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ParseError as e:
            raise SyntaxException(str(e)) from e
        except SchemaError as e:
            raise SchemaException(str(e)) from e
        except OptimizeError as e:
            raise UnsupportedError(str(e)) from e
        except Exception as e:
            raise ParSEvalError(str(e)) from e

    return wrapper


def non_fatal(
    *,
    default=None,
    default_from_args: Optional[Callable[..., object]] = None,
    catch: Optional[Iterable[Type[Exception]]] = None,
    log: bool = False,
) -> Callable:
    """
    Decorator that makes a function non-fatal:
    - Exceptions are caught and suppressed
    - A default value is returned instead
    Parameters
    ----------
    default:
        Value returned when an exception is caught.
    catch:
        Iterable of exception types to catch.
        Defaults to (Exception,).
    log:
        Whether to log the exception.
    """
    exceptions: ExceptionTypes = tuple(catch) if catch is not None else (Exception,)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                if log:
                    logger.debug(
                        "[non_fatal] Ignored error in %s: %s",
                        func.__qualname__,
                        e,
                        exc_info=True,
                    )
                if default_from_args is not None:
                    return default_from_args(*args, **kwargs)

                return default

        return wrapper

    return decorator


@dataclass
class RunResult:
    q1: str
    q2: str
    host_or_path: str
    db_id: str
    q1_result: ExecutionResult
    q2_result: ExecutionResult
    state: str
    set_semantic: bool
    error_msg: str = ""
    reuse_hit: bool = False
    database_source: str = "none"
    database_name: str | None = None


@dataclass
class ExecutionResult:
    host_or_path: str
    db_id: str
    query: str
    rows: List[Tuple[Any, ...]]
    elapsed_time: float
    error_msg: str = ""
    dialect: str = "sqlite"

    def is_equivalent(self, other: ExecutionResult, set_semantic=False) -> bool:
        if self.error_msg or other.error_msg:
            return False

        if set_semantic:
            return set(self.rows) == set(other.rows)
        else:
            return self.rows == other.rows
