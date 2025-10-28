from __future__ import annotations

from .adapter import SolverAdapter, SolverResult, ValueAssignment
import z3
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime
from src.parseval.dtype import DataType
from src.parseval.symbol import Variable, Symbol, Condition, Const

import logging

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
SECONDS_PER_MONTH = 30 * SECONDS_PER_DAY  # approximate month
SECONDS_PER_YEAR = 365 * SECONDS_PER_DAY  # approximate year


class SMTSolver(SolverAdapter):
    _SQL_OP_MAP = {
        "ADD": lambda a, b: a + b,
        "SUB": lambda a, b: a - b,
        "MUL": lambda a, b: a * b,
        "DIV": lambda a, b: a / b,
        "FLOORDIV": lambda a, b: a // b,
        "MOD": lambda a, b: a % b,
        "POW": lambda a, b: a**b,
        "EQ": lambda a, b: a == b,
        "NE": lambda a, b: a != b,
        "LT": lambda a, b: a < b,
        "LE": lambda a, b: a <= b,
        "GT": lambda a, b: a > b,
        "GE": lambda a, b: a >= b,
        "AND": lambda a, b: z3.And(a, b),
        "OR": lambda a, b: z3.Or(a, b),
        "NOT": lambda a: z3.Not(a),
        "LIKE": "LIKE",
    }

    def __init__(self, name: str):
        super().__init__(name)

    def supports(
        self, variables: List[Variable], constraints: List[Condition], context
    ):
        return True

    def solve(
        self, variables: List[Variable], constraints: List[Condition], context=None
    ):

        context = context if context is not None else {}
        context["variable_to_z3"] = {}
        ctx = context.get("z3_ctx", None)
        z3_constraints = []
        for constraint in constraints:
            z3_constraint = self._to_z3_constraint(constraint, ctx=ctx, context=context)
            z3_constraints.append(z3_constraint)
        solver = z3.Solver(ctx=ctx)
        solver.add(*z3_constraints)

        solver.add(*context.get("safe_divisions", []))
        solver.add(*context.get("str_format", []))
        solver.add(*context.get("datetime_format", []))

        for var_name, z3var in context.get("variable_to_z3", {}).items():
            if var_name in context.get("models", {}):
                solver.add(z3var == context["models"][var_name])

        sexpr = solver.sexpr()

        status = solver.check()
        assignments = []

        # logging.info(solver.sexpr())

        if status == z3.sat:
            model = solver.model()
            for var_name, z3var in context.get("variable_to_z3", {}).items():

                assignments.append(
                    ValueAssignment(
                        column=var_name,
                        alias="",
                        value=self._to_concrete(z3var, model.evaluate(z3var), context),
                        data_type="",
                        metadata={},
                    )
                )
            # for d in model.decls():
            #     assignments.append(
            #         ValueAssignment(
            #             column=d.name(),
            #             alias="",
            #             value=self._to_concrete(d, model[d], context),
            #             data_type="",
            #             metadata={},
            #         )
            #     )
        # else:
        #     logging.info(solver.sexpr())

        with open("tests/db/smt_debug.smt2", "a") as f:
            f.write(sexpr + "\n")
            f.write(str(status) + "\n")
            f.write(str(assignments) + "\n")
            f.write("\n\n")

        return SolverResult(status=str(status), assignments=assignments)

    def _to_z3_constraint(self, condition: Symbol, ctx, context) -> z3.BoolRef:

        if isinstance(condition, Variable):
            if condition.name not in context.get("variable_to_z3", {}):
                z3var = self._declare_sort(condition, context=context, ctx=ctx)
                context.setdefault("variable_to_z3", {})[condition.name] = z3var
                context.setdefault("z3_to_variable", {})[str(z3var)] = condition

            return context["variable_to_z3"][condition.name]
        if isinstance(condition, Const):
            if condition.dtype.is_type(DataType.Type.BOOLEAN):
                return z3.BoolVal(condition.value, ctx=ctx)
            elif condition.datatype.is_type(*DataType.INTEGER_TYPES):
                return z3.IntVal(condition.value, ctx=ctx)
            elif condition.datatype.is_type(*DataType.REAL_TYPES):
                return z3.RealVal(condition.value, ctx=ctx)
            if condition.dtype.is_type(*DataType.TEXT_TYPES):
                return z3.StringVal(str(condition.value), ctx=ctx)
            elif condition.datatype.is_type(*DataType.TEMPORAL_TYPES):
                for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                    try:
                        dt_value = datetime.strptime(condition.value, fmt)
                        return int(dt_value.timestamp())
                    except ValueError:
                        continue
                raise ValueError(f"Invalid date format: {condition.value}")

        if condition.key.upper() in self._SQL_OP_MAP:
            op = self._SQL_OP_MAP[condition.key.upper()]
            args = []
            for arg in condition.args:
                if isinstance(arg, Symbol):
                    arg_z3 = self._to_z3_constraint(arg, ctx=ctx, context=context)
                else:
                    arg_z3 = arg  # constant
                args.append(arg_z3)
            if condition.key.upper() in {"DIV", "FLOORDIV"}:
                safe_div_constraint = self._ensure_safe_div(args[1])
                context.setdefault("safe_divisions", []).append(safe_div_constraint)

            if callable(op):
                return op(*args)
            else:
                raise NotImplementedError(f"Operation {condition.key} not implemented")
        elif condition.key.upper() == "DISTINCT":
            args = []
            for arg in condition.args:
                if isinstance(arg, Symbol):
                    arg_z3 = self._to_z3_constraint(arg, ctx=ctx, context=context)
                else:
                    arg_z3 = arg  # constant
                    raise ValueError("DISTINCT only supports Symbol arguments")
                args.append(arg_z3)
            from functools import reduce

            cons = []
            for i in range(1, len(args)):
                cons.append(args[0] != args[i])
            return z3.And(cons)  # z3.Distinct(*args)
        else:
            raise NotImplementedError(
                f"{repr(condition)} not supported in SMT conversion"
            )

    def _coerce_to_sort(self, expr, target_sort):
        if expr.sort() == target_sort:
            return expr
        if target_sort == z3.IntSort():
            return z3.IntVal(int(str(expr)))
        if target_sort == z3.StringSort():
            return z3.StringVal(str(expr))
        if target_sort == z3.RealSort():
            return z3.RealVal(float(str(expr)))
        return expr

    def _declare_sort(self, variable, context, ctx=None) -> z3.SortRef:
        dtype: DataType = variable.datatype
        z3var = None
        if dtype.is_type(*DataType.INTEGER_TYPES):
            return z3.Int(variable.name, ctx=ctx)
        elif dtype.is_type(*DataType.REAL_TYPES):
            return z3.Real(variable.name, ctx=ctx)
        elif dtype.is_type(DataType.Type.BOOLEAN):
            return z3.Bool(variable.name, ctx=ctx)
        elif dtype.is_type(*DataType.TEXT_TYPES):
            z3var = z3.String(variable.name, ctx=ctx)
            self._ensure_str_printable(z3var, context)
            return self._ensure_str_length(z3var, dtype.length or 0, context)
        elif dtype.is_type(*DataType.TEMPORAL_TYPES):
            z3var = z3.Int(variable.name, ctx=ctx)
            return self._ensure_dt_format(z3var, context)
        else:
            raise RuntimeError(f"Unsupported data type: {dtype}")

    # def _ensure_str_no_leading_whitespace(self, s, context) -> z3.BoolRef:

    def _ensure_str_printable(self, s, context) -> z3.BoolRef:
        ascii_printable = z3.Range(chr(32), chr(126))
        ascii_printable_word = z3.Plus(ascii_printable)  # allows zero or more
        constraint = z3.InRe(s, ascii_printable_word)
        context.setdefault("str_format", []).append(constraint)
        return s

    def _ensure_str_length(self, s, length: int, context) -> z3.BoolRef:
        if isinstance(s.sort(), z3.SeqSortRef):
            # z3.Or(z3.Or(z3.Length(s) == 0, z3.SubString(s, 0, 1) != z3.StringVal(" ")))

            context.setdefault("str_format", []).append(z3.Length(s) > length)
            context.setdefault("str_format", []).append(
                z3.SubString(s, 0, 1) != z3.StringVal(" ")
            )
        return s

    def _ensure_dt_format(self, s, context) -> z3.BoolRef:
        context.setdefault("datetime_format", []).append(
            s > datetime(1970, 1, 1, 0, 0, 0).timestamp()
        )
        return s

    def _ensure_safe_div(self, denominator) -> z3.BoolRef:
        return denominator != 0

    def _to_concrete(self, decl, z3val, context):
        z3_to_variable = context.get("z3_to_variable", {})
        if isinstance(z3val, z3.FuncInterp):
            return self._to_concrete(z3val.else_value())
        variable = z3_to_variable[str(decl)]
        if variable.datatype.is_type(*DataType.TEMPORAL_TYPES):
            from datetime import datetime

            ts = z3val.as_long()
            concrete = datetime.fromtimestamp(ts)
        elif variable.datatype.is_type(*DataType.TEXT_TYPES):
            concrete = z3val.as_string()
        elif variable.datatype.is_type(DataType.Type.BOOLEAN):
            concrete = bool(z3val)
        elif variable.datatype.is_type(*DataType.INTEGER_TYPES):
            concrete = z3val.as_long()
        elif variable.datatype.is_type(*DataType.REAL_TYPES):
            concrete = z3val.as_decimal(prec=32)
            concrete = concrete[:-1] if concrete.endswith("?") else concrete
            concrete = float(concrete)
        else:
            raise RuntimeError(f"Cannot interpret {z3val}")
        return concrete
