"""ParSEval-specific sqlglot AST extension nodes.

These are custom ``sqlglot.exp.Expression`` subclasses that ParSEval uses
to model SQL constructs sqlglot doesn't expose in a convenient form. They
are **AST-only** — no evaluation semantics, no runtime state. The
evaluator in :mod:`parseval.plan.rex` dispatches on these classes the
same way it dispatches on sqlglot's built-in nodes.

Currently this module defines the ``IS NULL`` / ``IS NOT NULL`` predicate
forms. Additional dialect-specific AST extensions should land here rather
than in :mod:`parseval.plan.rex`, which is reserved for the evaluator +
the :class:`Symbol` family.
"""

from __future__ import annotations

from sqlglot import exp


class Is_Null(exp.Unary, exp.Predicate):
    """``<expr> IS NULL`` predicate.

    sqlglot parses ``IS NULL`` as ``exp.Is(this=<expr>, expression=exp.Null())``.
    ParSEval uses a dedicated class so the evaluator / extractor can
    dispatch on a single concept (rather than pattern-matching the
    generic ``exp.Is`` node) for the two common NULL predicates.
    """

    def sql(self, dialect=None, **opts):
        return f"{self.this.sql(dialect=dialect, **opts)} IS NULL"


class Is_Not_Null(exp.Unary, exp.Predicate):
    """``<expr> IS NOT NULL`` predicate; see :class:`Is_Null`."""

    def sql(self, dialect=None, **opts):
        return f"{self.this.sql(dialect=dialect, **opts)} IS NOT NULL"


__all__ = ["Is_Null", "Is_Not_Null"]
