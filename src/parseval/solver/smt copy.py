from __future__ import annotations
from typing import TYPE_CHECKING

from src.parseval.plan.rex import Expression
from typing import Dict, Optional, Any
import z3, operator
from sqlglot import exp as sqlglot_exp
from sqlglot.expressions import DataType

z3.set_option(html_mode=False)
z3.set_option(rational_to_decimal=True)
z3.set_option(precision=32)
z3.set_option(max_width=21049)
z3.set_option(max_args=100)


class SMTEncoder:
    SYMBOLIC_EVAL_REGISTRY = {
        "eq": lambda left, right: left.eq(right),
        "neq": lambda left, right: left.ne(right),
        "gt": operator.gt,
        "lt": operator.lt,
        "lte": operator.le,
        "gte": operator.ge,
        "like": lambda left, right: left.like(right),
        "and": lambda left, right: left.and_(right),
        "or": lambda left, right: left.or_(right),
        "add": operator.add,
        "sub": operator.sub,
        "mul": operator.mul,
        "div": operator.truediv,
    }

    def __init__(
        self,
        variables: Optional[Dict[str, z3.ExprRef]] = None,
        symbols: Optional[Dict[str, z3.ExprRef]] = None,
    ):
        self.var_cache: Dict[str, Any] = variables if variables is not None else {}
        self.symbol_cache: Dict[str, z3.ExprRef] = (
            symbols if symbols is not None else {}
        )

    def visit(self, expr, parent_stack=None, context=None):
        if expr is None:
            return None
        parent_stack = parent_stack or []
        context = context if context is not None else {}
        handler = getattr(self, f"visit_{expr.key}", self.generic_visit)
        result = handler(expr, parent_stack, context)
        return result

    def generic_visit(self, expr, parent_stack, context):
        if isinstance(expr, sqlglot_exp.Predicate):
            return self.visit_predicate(expr, parent_stack, context)
        elif isinstance(expr, sqlglot_exp.Binary):
            return self.visit_binary(expr, parent_stack, context)
        raise NotImplementedError(f"No visit_{expr.key} method defined")

    def visit_columnref(self, expr, parent_stack=None, context=None):
        if expr.this in self.symbol_cache:
            return self.symbol_cache[expr.this]

        # Create Z3 variable based on type
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            z3_var = z3.Int(expr.this)
        elif expr.dtype.is_type(*DataType.REAL_TYPES):
            z3_var = z3.Real(expr.this)
        elif expr.dtype.is_type("BOOLEAN"):
            z3_var = z3.Bool(expr.this)
        elif expr.dtype.is_type(*DataType.TEXT_TYPES):
            z3_var = z3.String(expr.this)
        elif expr.dtype.is_type(*DataType.TEMPORAL_TYPES):
            z3_var = z3.String(expr.this)  # Use String for temporal types
        else:
            raise TypeError(f"Unsupported type for Z3: {expr.dtype}")
        self.symbol_cache[expr.this] = z3_var
        self.var_cache[expr.this] = expr
        return z3_var

    def visit_literal(self, expr: sqlglot_exp.Literal, parent_stack=None, context=None):
        value = expr.this
        if expr.is_int:
            return int(value)
        elif expr.is_number:
            return float(value)
        elif expr.is_string:
            return str(value)
        elif expr.is_date or expr.is_time or expr.is_timestamp:
            from datetime import datetime

            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                # fallback for timestamp formats
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
                return value  # leave as string if unparseable
        else:
            return value

    def visit_predicate(self, expr: sqlglot_exp.Predicate, parent_stack, context):
        left = self.visit(
            expr.this, parent_stack=parent_stack + [expr], context=context
        )
        right = self.visit(
            expr.expression, parent_stack=parent_stack + [expr], context=context
        )
        smt_expr = self.SYMBOLIC_EVAL_REGISTRY[expr.key](left, right)
        return smt_expr

    def visit_binary(self, expr: sqlglot_exp.Binary, parent_stack, context):
        left = self.visit(
            expr.this, parent_stack=parent_stack + [expr], context=context
        )
        right = self.visit(
            expr.expression, parent_stack=parent_stack + [expr], context=context
        )
        smt_expr = self.SYMBOLIC_EVAL_REGISTRY[expr.key](left, right)
        return smt_expr

    def visit_not(self, expr: sqlglot_exp.Not, parent_stack=None, context=None):
        this = self.visit(
            expr.this, parent_stack=parent_stack + [expr], context=context
        )
        return this.not_()

    def visit_subquery(self, expr, parent_stack, context):
        sub_ctx = {
            "ref_conditions": [],
            "sql_conditions": [],
            "smt_conditions": [],
            "parent": expr,
        }
        for child in expr.expressions:
            self.visit(child, parent_stack + [expr], sub_ctx)
        # Merge subquery predicates into main context
        # context["predicates"].extend(sub_ctx["predicates"])
        # context["columns"].extend(sub_ctx["columns"])
        # context["smt_constraints"].extend(sub_ctx["smt_constraints"])
        return expr

    # def visit_subquery(self, expr: sqlglot_exp.Subquery):
    #     for query in expr.query:

    #         print(query.pprint())
    #         res = query.accept(self.plan_encoder)

    #         logging.info(f"Subquery result rows: {len(res.data)}, {res}")
    #         return res.data[0][0]


class SMTSolver:
    if TYPE_CHECKING:
        from src.parseval.instance import Instance

    def __init__(
        self,
        context=None,
        timeout: int = 3000,
        debug=True,
    ):
        self.solver = z3.Solver(ctx=context)
        self.solver.set("timeout", timeout)

        self.variables = {}
        self.constraints = []

    def check(self): ...

    def before_solve(self): ...

    def after_solve(self, model: z3.ModelRef): ...

    def ensure_printable(self, s):
        ascii_printable = z3.Range(chr(32), chr(126))
        ascii_printable_word = z3.Plus(ascii_printable)  # allows zero or more
        constraint = z3.InRe(s, ascii_printable_word)
        return constraint

    def _to_concrete(self, z3val):
        if isinstance(z3val, z3.FuncInterp):
            return self._to_concrete(z3val.else_value())
        sort = z3val.sort().name()
        concrete = None
        if sort == "Int":
            concrete = z3val.as_long()
        elif sort == "Real":
            concrete = z3val.as_decimal(prec=32)
            concrete = concrete[:-1] if concrete.endswith("?") else concrete
            concrete = float(concrete)
        elif sort == "Bool":
            concrete = bool(z3val)
        elif sort == "String":
            concrete = z3val.as_string()
        else:
            raise RuntimeError(f"Cannot interpret {z3val}")
        return concrete
