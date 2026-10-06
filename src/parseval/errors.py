from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NoReturn


class ErrorCode(str, Enum):
    PARSE_ERROR = "parse_error"
    QUALIFICATION_ERROR = "qualification_error"
    TYPE_ERROR = "type_error"
    UNKNOWN_TABLE = "unknown_table"
    UNKNOWN_COLUMN = "unknown_column"
    AMBIGUOUS_COLUMN = "ambiguous_column"
    UNKNOWN_FUNCTION = "unknown_function"
    UNKNOWN_AGGREGATE = "unknown_aggregate"
    UNSUPPORTED_QUERY = "unsupported_query"
    UNSUPPORTED_SOURCE = "unsupported_source"
    UNSUPPORTED_JOIN = "unsupported_join"
    UNSUPPORTED_EXPRESSION = "unsupported_expression"
    UNSUPPORTED_AGGREGATION = "unsupported_aggregation"
    INVALID_LIMIT = "invalid_limit"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_IR = "unsupported_ir"
    FILE_ACCESS = "file_access"
    CATALOG_ERROR = "catalog_error"
    DDL_IMPORT = "ddl_import"
    IR_VALIDATION = "ir_validation"
    UEXPR_TRANSLATION = "uexpr_translation"
    INTERNAL_ERROR = "internal_error"


class ErrorKind(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"
    WARNING = "warning"


class ErrorPhase(str, Enum):
    INPUT = "input"
    CATALOG = "catalog"
    LOWERING = "lowering"
    TRANSLATION = "translation"
    OUTPUT = "output"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: ErrorCode
    kind: ErrorKind
    phase: ErrorPhase
    severity: DiagnosticSeverity
    message: str
    node_type: str | None = None
    sql: str | None = None

    def __str__(self) -> str:
        pieces = [f"[{self.code.value}] {self.message}"]
        if self.node_type:
            pieces.append(f"node={self.node_type}")
        if self.sql:
            pieces.append(f"sql={self.sql}")
        return "; ".join(pieces)


class SubEqError(Exception):
    """An expected, structured failure at a SubEq subsystem boundary."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(str(diagnostic))


class _DomainError(SubEqError):
    code: ErrorCode
    kind: ErrorKind
    phase: ErrorPhase

    def __init__(self, message: str) -> None:
        super().__init__(
            make_diagnostic(
                self.code,
                message,
                kind=self.kind,
                phase=self.phase,
            )
        )


class ScalarTypeError(_DomainError):
    code = ErrorCode.TYPE_ERROR
    kind = ErrorKind.INVALID_INPUT
    phase = ErrorPhase.INPUT


class CatalogError(_DomainError):
    code = ErrorCode.CATALOG_ERROR
    kind = ErrorKind.INVALID_INPUT
    phase = ErrorPhase.CATALOG


class DDLImportError(CatalogError):
    code = ErrorCode.DDL_IMPORT


class IRValidationError(_DomainError):
    code = ErrorCode.IR_VALIDATION
    kind = ErrorKind.INVALID_INPUT
    phase = ErrorPhase.INPUT


class UExprTranslationError(_DomainError):
    code = ErrorCode.UEXPR_TRANSLATION
    kind = ErrorKind.UNSUPPORTED
    phase = ErrorPhase.TRANSLATION


def make_diagnostic(
    code: ErrorCode,
    message: str,
    *,
    kind: ErrorKind,
    phase: ErrorPhase,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    node: object | None = None,
    sql: str | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        kind=kind,
        phase=phase,
        severity=severity,
        message=message,
        node_type=type(node).__name__ if node is not None else None,
        sql=sql,
    )


def expect(
    condition: bool,
    message: str,
    *,
    error: type[_DomainError] = IRValidationError,
) -> None:
    """Raise ``error(message)`` unless ``condition`` holds.

    Centralizes the many ad-hoc ``if not X: raise SomeDomainError(...)``
    checks scattered across the codebase into a single call site per check.
    Defaults to IRValidationError since that's the most common caller,
    but any `_DomainError` subclass (CatalogError, UExprTranslationError,
    etc.) can be passed for module-specific validation.
    """
    if not condition:
        raise error(message)


def fail(
    code: ErrorCode,
    message: str,
    *,
    node: object | None = None,
    sql: str | None = None,
    cause: BaseException | None = None,
) -> NoReturn:
    kind = (
        ErrorKind.UNSUPPORTED
        if code.value.startswith("unsupported")
        else ErrorKind.INTERNAL
        if code is ErrorCode.INTERNAL_ERROR
        else ErrorKind.INVALID_INPUT
    )
    error = SubEqError(
        make_diagnostic(
            code,
            message,
            kind=kind,
            phase=ErrorPhase.LOWERING,
            node=node,
            sql=sql,
        )
    )
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "Diagnostic",
    "DiagnosticSeverity",
    "CatalogError",
    "DDLImportError",
    "ErrorCode",
    "ErrorKind",
    "ErrorPhase",
    "IRValidationError",
    "SubEqError",
    "UExprTranslationError",
    "fail",
    "expect",
    "make_diagnostic",
]
