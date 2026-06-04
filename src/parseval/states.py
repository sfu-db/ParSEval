from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

logger = logging.getLogger("parseval")


# =============================================================================
# Enums
# =============================================================================


class ParSEvalState(Enum):
    INITIAL = "initial"
    PARSING = "parsing"
    VALIDATING = "validating"
    TRANSFORMING = "transforming"
    COMPLETED = "completed"
    ERROR = "error"


class Verdict(Enum):
    """Result of equivalence checking."""
    EQ = "eq"              # Queries are equivalent on generated instance
    NEQ = "neq"            # Queries produce different results
    SYNTAX_ERROR = "syntax_error"  # One or both queries have syntax errors
    RUNTIME_ERROR = "runtime_error"  # Execution error (not syntax)
    TIMEOUT = "timeout"    # Generation or execution timed out
    UNKNOWN = "unknown"    # Could not determine


class Semantics(Enum):
    """How to compare query results."""
    BAG = "bag"    # Order + duplicates matter (multiset)
    SET = "set"    # Only distinct tuples matter (set)


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class ExecutionResult:
    """Result of executing a single query."""
    query: str
    rows: List[Tuple[Any, ...]] = field(default_factory=list)
    error_msg: str = ""
    elapsed_time: float = 0.0

    @property
    def is_error(self) -> bool:
        return bool(self.error_msg)

    @property
    def is_syntax_error(self) -> bool:
        if not self.error_msg:
            return False
        lower = self.error_msg.lower()
        return any(k in lower for k in ("syntax", "parse", "near", "no such "))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "rows": self.rows,
            "error_msg": self.error_msg,
            "elapsed_time": self.elapsed_time,
        }

@dataclass
class GenerationResult:
    """Result of test database generation."""
    success: bool
    rows_generated: int = 0
    coverage: float = 0.0
    error_msg: str = ""
    elapsed_time: float = 0.0
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "rows_generated": self.rows_generated,
            "coverage": self.coverage,
            "error_msg": self.error_msg,
            "elapsed_time": self.elapsed_time,
        }


@dataclass
class DisproveResult:
    """Result of equivalence disproval attempt."""
    verdict: Verdict
    semantics: Semantics
    q1_result: ExecutionResult
    q2_result: ExecutionResult
    generation: GenerationResult
    connection_string: str = ""
    error_msg: str = ""

    @property
    def is_equivalent(self) -> bool:
        return self.verdict == Verdict.EQ

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        return {
            "verdict": self.verdict.value,
            "semantics": self.semantics.value,
            "q1_result": self.q1_result.to_dict(),
            "q2_result": self.q2_result.to_dict(),
            "generation": self.generation.to_dict(),
            "connection_string": self.connection_string,
            "error_msg": self.error_msg,
        }

@dataclass
class InstantiateResult:
    """Result of database instantiation."""
    success: bool
    generation: GenerationResult
    connection_string: str = ""
    db_id: str = ""
    error_msg: str = ""


# =============================================================================
# Comparison Logic
# =============================================================================


def compare_results(
    r1: ExecutionResult,
    r2: ExecutionResult,
    semantics: Semantics = Semantics.BAG,
) -> Verdict:
    """Compare two execution results and return a verdict."""
    # Syntax errors
    if r1.is_error or r2.is_error:
        return Verdict.SYNTAX_ERROR

    # Compare based on semantics
    if semantics == Semantics.SET:
        eq = set(r1.rows) == set(r2.rows)
    else:
        # BAG: sort both (order-independent multiset comparison)
        try:
            eq = sorted(r1.rows) == sorted(r2.rows)
        except TypeError:
            # Unsortable (e.g., None values) — compare as-is
            eq = r1.rows == r2.rows

    return Verdict.EQ if eq else Verdict.NEQ


# =============================================================================
# Exceptions
# =============================================================================


class ParSEvalError(Exception):
    """Base exception for ParSEval-related errors."""
    pass


class SchemaException(ParSEvalError):
    """Schema-related errors (missing columns, invalid definitions)."""
    pass


class SyntaxException(ParSEvalError):
    """Syntax errors in SQL input."""
    pass


class ValidationException(ParSEvalError):
    """Validation failures (constraints, integrity)."""
    pass


# =============================================================================
# Decorators
# =============================================================================


from sqlglot.errors import ParseError, SchemaError, OptimizeError, UnsupportedError

ExceptionTypes = Tuple[Type[BaseException], ...]


def raise_exception(func_or_msg):
    """Decorator or direct call to raise ParSEvalError."""
    if isinstance(func_or_msg, str):
        raise ParSEvalError(func_or_msg)

    @functools.wraps(func_or_msg)
    def wrapper(*args, **kwargs):
        try:
            return func_or_msg(*args, **kwargs)
        except ParseError as e:
            raise SyntaxException(str(e)) from e
        except SchemaError as e:
            raise SchemaException(str(e)) from e
        except OptimizeError as e:
            raise ParSEvalError(str(e)) from e
        except ParSEvalError:
            raise
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
    """Decorator that catches exceptions and returns a default value."""
    exceptions: ExceptionTypes = tuple(catch) if catch is not None else (Exception,)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                if log:
                    logger.debug("[non_fatal] %s: %s", func.__qualname__, e, exc_info=True)
                if default_from_args is not None:
                    return default_from_args(*args, **kwargs)
                return default

        return wrapper

    return decorator


# Legacy compat
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
    elapsed_time: float = 0.0
