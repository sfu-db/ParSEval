from __future__ import annotations
from typing import Dict, Optional, TYPE_CHECKING, Callable
from abc import ABC, abstractmethod


class SymbolicEncoder(ABC):
    """Interface for creating and manipulating symbolic expressions."""

    @abstractmethod
    def apply_operator(self, operator: str, *operands):
        """Apply an operator to symbolic operands."""
        pass

    @abstractmethod
    def track_predicate(self, expr, result):
        """Optionally track predicates for branch coverage."""
        pass


class DefaultSymbolicEncoder(SymbolicEncoder):
    """Default implementation of SymbolicEncoder."""

    DEFAULT_BRANCH_EXPRESSIONS: Dict[str, Callable] = {
        "eq": lambda *args: args[0].eq(args[1]),
        "neq": lambda *args: args[0].ne(args[1]),
        "gt": lambda *args: args[0] > args[1],
        "lt": lambda *args: args[0] < args[1],
        "lte": lambda *args: args[0] >= args[1],
        "gte": lambda *args: args[0] <= args[1],
        "like": lambda *args: args[0].like(args[1]),
    }

    DEFAULT_OPAQUE_EXPRESSIONS: Dict[str, Callable] = {
        "and": lambda *args: args[0].and_(args[1]),
        "or": lambda *args: args[0].or_(args[1]),
        "add": lambda *args: args[0] + args[1],
        "sub": lambda *args: args[0] - args[1],
        "mul": lambda *args: args[0] * args[1],
        "div": lambda *args: args[0] // args[1],
        "floordiv": lambda *args: args[0] // args[1],
        "strftime": lambda operand, fmt: operand.strftime(fmt),
    }

    def __init__(
        self,
        branch_handlers: Optional[Dict[str, Callable]] = None,
        opaque_handlers: Optional[Dict[str, Callable]] = None,
    ):
        super().__init__()
        self.branch_expression_handlers: Dict[str, Callable] = dict(
            self.DEFAULT_BRANCH_EXPRESSIONS
        )
        self.opaque_expression_handlers: Dict[str, Callable] = dict(
            self.DEFAULT_OPAQUE_EXPRESSIONS
        )
        self.register_handlers(branch=branch_handlers, opaque=opaque_handlers)
        self.symbol_scopes = [{}]  # Stack of symbol scopes

    def register_handlers(
        self,
        branch: Optional[Dict[str, Callable]] = None,
        opaque: Optional[Dict[str, Callable]] = None,
    ):
        """Register or update handler mappings after initialization."""
        if branch:
            self.branch_expression_handlers.update(branch)
        if opaque:
            self.opaque_expression_handlers.update(opaque)

    def push_scpope(self):
        self.symbol_scopes.append({})

    def pop_scope(self):
        self.symbol_scopes.pop()

    def apply_operator(self, operator: str, *operands):
        # Simple implementation that just returns a tuple
        return (operator, operands)

    def track_predicate(self, expr, result):
        # No-op in default implementation
        pass
