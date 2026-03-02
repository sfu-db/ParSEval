from __future__ import annotations

import z3, logging
from typing import Dict, Any, List, Optional, TYPE_CHECKING, Callable, Tuple
from sqlglot.expressions import DataType
from sqlglot import exp

# from src.parseval.plan.rex import Variable
from datetime import datetime
from contextlib import contextmanager
from parseval.plan.rex import Const

logger = logging.getLogger("parseval.smt")


@contextmanager
def checkpoint(z3solver):
    z3solver.push()
    try:
        yield z3solver
    finally:
        z3solver.pop()


def infer(value: Any) -> "DataType":
    """Infer data type from a Python value"""
    if value is None:
        return DataType.build("NULL")
    if isinstance(value, bool):
        return DataType.build("BOOLEAN")
    elif isinstance(value, int):
        return DataType.build("INT")
    elif isinstance(value, float):
        return DataType.build("FLOAT")
    elif isinstance(value, str):
        return DataType.build("TEXT", length=len(value))
    else:
        return DataType.build("TEXT")


def make_option_type(
    name, inner_sort: Optional[z3.Sort], z3ctx: Optional[z3.Context] = None
) -> z3.SortRef:
    """
    Create Option(T) datatype.
    OptionInt = NULL | Some(value: Int)
    Parameters
    ----------
    name : str
        Name of datatype (must be unique in Z3 context)
    inner_sort : z3.SortRef
        Base Z3 sort
    Returns
    -------
    z3.DatatypeSortRef
    """
    dtype = z3.Datatype(name, ctx=z3ctx)
    dtype.declare("NULL")
    dtype.declare("Some", ("value", inner_sort))
    return dtype.create()


def _to_z3_sort(dtype: DataType, z3ctx: Optional[z3.Context] = None) -> z3.SortRef:
    """
    Map SQL data types to Z3 sorts.
    Parameters        ----------
    dtype : DataType
        SQL data type to map
    z3ctx : Optional[z3.Context]
        Z3 context to create sorts in (optional)
    Returns
    -------
    z3.SortRef
    """
    dtype = DataType.build(dtype)
    if dtype.is_type(*DataType.INTEGER_TYPES):
        return z3.IntSort(z3ctx)
    elif dtype.is_type(*DataType.REAL_TYPES):
        return z3.RealSort(z3ctx)
    elif dtype.is_type(DataType.Type.BOOLEAN):
        return z3.BoolSort(z3ctx)
    elif dtype.is_type(*DataType.TEXT_TYPES):
        return z3.StringSort(z3ctx)
    elif dtype.is_type(*DataType.TEMPORAL_TYPES):
        return z3.IntSort(z3ctx)
    else:
        raise RuntimeError(f"Unsupported data type: {repr(dtype)}")


def _to_z3val(dtype: DataType, value, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
    dtype = DataType.build(dtype)
    if str(dtype) == "UNKNOWN":
        dtype = infer(value)
    try:
        base_sort = _to_z3_sort(dtype, z3ctx)
        option_sort = OptionTypeRegistry.get(base_sort)
    except Exception as e:
        print(
            f"Error creating Z3 sort for data type {dtype}: {dtype}, {value}, {repr(dtype.parent)}"
        )
        raise e
    if value is None:
        return option_sort.NULL
    if dtype.is_type(*DataType.INTEGER_TYPES):
        return option_sort.Some(z3.IntVal(int(value), ctx=z3ctx))
    elif dtype.is_type(*DataType.REAL_TYPES):
        return z3.RealVal(float(value), ctx=z3ctx)
    elif dtype.is_type(DataType.Type.BOOLEAN):
        return z3.BoolVal(bool(value), ctx=z3ctx)
    elif dtype.is_type(*DataType.TEXT_TYPES):
        return option_sort.Some(z3.StringVal(str(value), ctx=z3ctx))
        return z3.StringVal(str(value), ctx=z3ctx)
    elif dtype.is_type(*DataType.TEMPORAL_TYPES):
        return z3.IntVal(int(value), ctx=z3ctx)
    else:
        raise RuntimeError(f"Unsupported data type: {dtype}")


class OptionTypeRegistry:

    _base_to_option = {}
    _sort_to_option = {}

    @classmethod
    def get(
        cls, base_sort: z3.SortRef, z3ctx: Optional[z3.Context] = None
    ) -> z3.DatatypeSortRef:
        key = str(base_sort).capitalize()
        if key not in cls._base_to_option:
            name = f"Option_{key}".replace(" ", "_")
            opt = make_option_type(name, base_sort, z3ctx=z3ctx)
            cls._base_to_option[key] = opt
            cls._sort_to_option[str(opt)] = opt
        return cls._base_to_option[key]

    @classmethod
    def from_sort(cls, option_sort):
        return cls._sort_to_option[str(option_sort)]

    @classmethod
    def is_option_sort(cls, sort):
        return str(sort) in cls._sort_to_option


def is_option_expr(expr: z3.SortRef):
    return OptionTypeRegistry.is_option_sort(expr.sort())


def option_of(expr):
    return OptionTypeRegistry.from_sort(expr.sort())


def unwrap_option(expr):
    opt = option_of(expr)
    return opt.value(expr)


def declare_sort(
    variable: exp.Column, z3ctx: Optional[z3.Context] = None
) -> z3.ConstRef:
    dtype = variable.type
    var_name = f"{variable.table}.{variable.name}"
    base_sort = _to_z3_sort(dtype, z3ctx)
    option_sort = OptionTypeRegistry.get(base_sort, z3ctx)
    z3var = z3.Const(var_name, option_sort)
    return z3var


def lift_is(expr1, expr2):
    null_checks = []
    raw_expr1 = expr1
    raw_expr2 = expr2
    if is_option_expr(expr1):
        opt1 = option_of(expr1)
        null_checks.append(expr1 == opt1.NULL)
        raw_expr1 = opt1.value(expr1)
    if is_option_expr(expr2):
        opt2 = option_of(expr2)
        null_checks.append(expr2 == opt2.NULL)
        raw_expr2 = opt2.value(expr2)
    if not null_checks:
        return raw_expr1 == raw_expr2
    return z3.If(z3.Or(*null_checks), z3.BoolVal(True), raw_expr1 == raw_expr2)
    ...


def lift_options(func, *args):
    raw_args = []
    null_checks = []
    for a in args:
        if is_option_expr(a):
            opt = option_of(a)
            null_checks.append(opt.is_NULL(a))
            raw_args.append(opt.value(a))
        else:
            raw_args.append(a)

    return z3.And(z3.Not(z3.Or(*null_checks)), func(*raw_args))

    # if not null_checks:
    #         return func(*raw_args)
    #     # return z3.If(z3.Not(z3.Or(*null_checks)), func(*raw_args), z3.BoolVal(True))
    return z3.And(z3.Not(z3.Or(*null_checks)), func(*raw_args))
    return func(*raw_args)


#


def null_if_any(bsort, *args):
    null_checks = []
    for a in args:
        if is_option_expr(a):
            opt = option_of(a)
            null_checks.append(a == opt.NULL)
    result_sort = OptionTypeRegistry.from_sort(bsort)

    return z3.If(
        z3.Or(*null_checks),
        result_sort.NULL,
    )


def like_to_z3(var, pattern: str):
    """
    Convert SQL LIKE pattern to Z3 regex constraint using native Z3 regex constructors.

    % -> any sequence of characters (ReStar)
    _ -> any single character (ReRange)
    Other characters -> literal character (Re)
    """
    """
    Convert a SQL LIKE pattern to Z3 constraints in a way that
    allows Z3 to generate realistic Python strings.
    """
    some_checks = []
    raw = var
    if is_option_expr(var):
        opt = option_of(var)
        some_checks.append(opt.is_Some(var))
        raw = opt.value(var)
    parts = []
    constraints = []
    pattern = pattern.as_string()

    for i, ch in enumerate(pattern):
        if ch == "_":
            c = z3.String(f"c{i}")
            constraints.append(z3.Length(c) == 1)
            parts.append(c)
        elif ch == "%":
            tail = z3.String(f"p{i}")
            constraints.append(z3.Length(tail) >= 1)
            parts.append(tail)
        else:
            parts.append(z3.StringVal(ch))
    expr = parts[0]
    for p in parts[1:]:
        expr = z3.Concat(expr, p)

    # The LIKE constraint is var == expr AND all length constraints
    constraints.append(raw == expr)
    return z3.And(*some_checks, *constraints)  # <- combine into a single Z3 expression


class OperationRegistry:
    _operations = {
        "GT": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: lift_options(lambda lv, rv: lv > rv, x, y),
            "concrete": lambda x, y: x > y,
            "nullable": "both",
            "return_type": "boolean",
        },
        "LT": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: lift_options(lambda lv, rv: lv < rv, x, y),
            "concrete": lambda x, y: x < y,
            "nullable": "both",
            "return_type": "boolean",
        },
        "GTE": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: lift_options(lambda lv, rv: lv >= rv, x, y),
            "concrete": lambda x, y: x >= y,
            "nullable": "both",
            "return_type": "boolean",
        },
        "LTE": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: lift_options(lambda lv, rv: lv <= rv, x, y),
            "concrete": lambda x, y: x <= y,
            "nullable": "both",
        },
        "EQ": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: lift_options(lambda lv, rv: lv == rv, x, y),
            "concrete": lambda x, y: x == y,
            "nullable": "both",
            "return_type": "boolean",
        },
        "NEQ": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: lift_options(lambda lv, rv: lv != rv, x, y),
            "concrete": lambda x, y: x != y,
            "nullable": "both",
            "return_type": "boolean",
        },
        "LIKE": {
            "type": "comparison",
            "arg_types": "any",
            "symbolic": lambda x, y: like_to_z3(x, y),
            "concrete": lambda x, y: x.like(y),
            "nullable": "both",
            "return_type": "boolean",
        },
        # Logical operations
        "AND": {
            "type": "logical",
            "arg_types": "any",
            "symbolic": lambda *args: z3.And(args[0], args[1]),
            "concrete": lambda *args: args[0] and args[1],
            "nullable": "propagate",
            "return_type": "boolean",  # Handle null propagation
        },
        "OR": {
            "type": "logical",
            "arg_types": "any",
            "symbolic": lambda *args: z3.Or(args[0], args[1]),
            "concrete": lambda *args: args[0] or args[1],
            "nullable": "propagate",
            "return_type": "boolean",
        },
        "NOT": {
            "type": "logical",
            "arg_types": "any",
            "symbolic": lambda *args: z3.Not(args[0]),
            "concrete": lambda *args: not args[0],
            "nullable": "propagate",
            "return_type": "boolean",
        },
        "DISTINCT": {
            "type": "logical",
            "arg_types": "any",
            "symbolic": lambda *args: z3.Distinct(*args),
            "concrete": lambda *args: not args[0],
            "nullable": "propagate",
            "return_type": "boolean",
        },
        "IS": {
            "type": "identity",
            "arg_types": "any",
            "symbolic": lambda *args: args[0] == args[1],
            "concrete": lambda *args: args[0] is args[1],
            "nullable": False,
            "return_type": "boolean",  # IS NULL is a special case
        },
        "LENGTH": {
            "type": "function",
            "arg_types": "any",
            "symbolic": lambda args: z3.Length(args[0]),
            "concrete": lambda args: len(args[0]),
            "nullable": "any",
            "return_type": "int",
        },
        "Abs": {
            "type": "function",
            "arg_types": "any",
            "symbolic": lambda args: z3.If(args[0] >= 0, args[0], -args[0]),
            "concrete": lambda args: abs(args[0]),
            "nullable": "any",
            "return_type": "int",
        },
        "CAST": {
            "type": "function",
            "arg_types": "any",
            "symbolic": lambda args: args[0],
            "concrete": lambda args: args[0],
            "nullable": "any",
            "return_type": "int",
        },
    }

    @classmethod
    def register(
        cls,
        name,
        symbolic_fn: Callable,
        concrete_fn: Callable,
        nullable="any",
        _type="comparison",
        arg_types: Optional[List[str]] = None,
        return_type: Optional[str] = None,
    ):

        cls._operations[name] = {
            "type": _type,
            "arg_types": arg_types,
            "symbolic": symbolic_fn,
            "concrete": concrete_fn,
            "nullable": nullable,
            "return_type": return_type,
        }

    @classmethod
    def get(cls, name) -> Dict[str, Any]:
        return cls._operations[name]

    @classmethod
    def eval_symbol(cls, name, *args):
        func = cls.get(name)
        return func["symbolic"](*args)

    @classmethod
    def eval_concrete(cls, name, *args):
        func = cls.get(name)
        return func["concrete"](*args)


# smt_exmpr = OperationRegistry.get("GT")['symbolic'](v1, z3.IntVal(30))
# val1 = _to_z3val(DataType.build("int"), 12)
# smt_exmpr = OperationRegistry.eval_symbol("EQ", v1, _to_z3val(DataType.build("int"), None))

# solver = z3.Solver()
# solver.add(smt_exmpr)
# solver.add(OperationRegistry.eval_symbol("EQ", v1, val1))

# print(smt_exmpr)
# print(solver.sexpr())
# print(solver.check())
# print(solver.model())

# print(solver.model().evaluate(v1))

# def lift_binary(op, left, right, z3ctx: Optional[z3.Context] = None):
#     if not (is_option_expr(left) or is_option_expr(right)):
#         return apply_raw_binary(op, left, right)
#     opt = option_of(left if is_option_expr(left) else right)
#     def null_check(x):
#         if is_option_expr(x):
#             o = option_of(x)
#             return x == o.NULL
#         return z3.BoolVal(False)
#     null_expr = z3.Or(null_check(left), null_check(right))
#     lv = unwrap_option(left) if is_option_expr(left) else left
#     rv = unwrap_option(right) if is_option_expr(right) else right
#     raw = apply_raw_binary(op, lv, rv)
#     if raw.sort() == z3.BoolSort():
#         return z3.And(z3.Not(null_expr), raw)
#     result_opt = OptionTypeRegistry.get(raw.sort())
#     return z3.If(null_expr, result_opt.NULL, result_opt.Some(raw))


class SMTSolver:
    def __init__(
        self, variables, z3ctx: Optional[z3.Context] = None, verbose: bool = False
    ):
        self.variables = variables
        self.verbose = verbose
        self.z3ctx = z3ctx
        self.solver = z3.Solver(ctx=self.z3ctx)
        self.model = None
        self.context = {}
        # self.uf = z3.UnionFind(ctx=self.z3ctx)

        z3.set_option(html_mode=False)
        z3.set_option(rational_to_decimal=True)
        z3.set_option(precision=32)
        z3.set_option(max_width=21049)
        z3.set_option(max_args=100)

    def add(self, constraint):

        try:
            if z3.is_bool(constraint):
                if self.verbose:
                    logger.info(constraint)
                self.solver.add(constraint)
        except Exception as e:
            print(f"Error adding constraint: {constraint}")
            raise e

    def solve(self):

        if self.solver.check() != z3.sat:
            return "unsat", {}
        with checkpoint(self.solver):
            for var_name, z3var in self.context.get("variable_to_z3", {}).items():
                column = self.context["z3_to_variable"][str(z3var)]
                dtype = DataType.build(column.type)
                if dtype.is_type(*DataType.TEMPORAL_TYPES):
                    self._ensure_dt_format(z3var)
                if dtype.is_type(*DataType.TEXT_TYPES):
                    self._ensure_str_printable(z3var)
                    self._ensure_str_length(z3var, 0)

        if self.solver.check() != z3.sat:
            return "unsat", {}
        self.solver.check()
        if self.solver.check() != z3.sat:
            return "unsat", {}
        self.model = self.solver.model()
        solutions = self.z3_to_python(self.model) or {}

        logger.info(f"SMT solver found solution: {solutions}")
        return "sat", solutions

    def _ensure_str_printable(self, s) -> z3.BoolRef:
        if is_option_expr(s) and option_of(s).value(s).sort() == z3.StringSort():
            s = unwrap_option(s)
            ascii_printable = z3.Range(chr(32), chr(126))
            ascii_printable_word = z3.Plus(ascii_printable)  # allows zero or more
            constraint = z3.InRe(s, ascii_printable_word)
            self.add(constraint)

    def _ensure_str_length(self, s, length: int) -> z3.BoolRef:
        if is_option_expr(s):

            os = unwrap_option(s)
            if isinstance(os.sort(), z3.SeqSortRef):
                opt1 = option_of(s)
                cccc = z3.And(
                    z3.Length(os) > z3.IntVal(length, ctx=self.z3ctx),
                    z3.SubString(os, 0, 1) != z3.StringVal(" ", ctx=self.z3ctx),
                )
                self.add(z3.Implies(opt1.is_Some(s), cccc, ctx=self.z3ctx))

    def _ensure_dt_format(self, s) -> z3.BoolRef:
        self.add(s > datetime(1970, 1, 1, 0, 0, 0).timestamp())
        self.add(s < datetime(2030, 1, 1, 0, 0, 0).timestamp())

    def _ensure_safe_div(self, denominator) -> z3.BoolRef:
        return denominator != 0

    def _to_z3_expr(self, condition: exp.Condition):
        condition = condition.this if isinstance(condition, exp.Paren) else condition

        if isinstance(condition, (exp.Column)):
            col_key = f"{condition.table}.{condition.name}"
            if col_key not in self.context.get("variable_to_z3", {}):
                variable = condition
                try:
                    z3var = declare_sort(variable, z3ctx=self.z3ctx)
                    self.context.setdefault("variable_to_z3", {})[col_key] = z3var
                    self.context.setdefault("z3_to_variable", {})[str(z3var)] = variable
                except Exception as e:
                    raise e
            return self.context["variable_to_z3"][col_key]
        elif isinstance(condition, exp.Null):
            return _to_z3val(condition.datatype, None, z3ctx=self.z3ctx)
        elif isinstance(condition, (exp.Literal, Const)):
            v = _to_z3val(condition.datatype, condition.this, z3ctx=self.z3ctx)
            return v
        elif condition.key.upper() in OperationRegistry._operations:
            args = []
            for arg in condition.iter_expressions():
                if not isinstance(arg, exp.DataType):
                    args.append(self._to_z3_expr(arg))
            return OperationRegistry.eval_symbol(condition.key.upper(), *args)
        else:
            raise NotImplementedError(
                f"{repr(condition)} not supported in SMT conversion, {type(condition)}"
            )

    def z3_to_python(self, model: z3.ModelRef):
        result = {}

        decls = [d.name() for d in model.decls()]
        logger.info(f"Model declarations: {decls}")
        for var_name, z3var in self.context.get("variable_to_z3", {}).items():
            if var_name not in decls:
                logger.info(f"Variable {var_name} not in model declarations, skipping")
                continue

            concrete = self._z3_to_python(
                model.evaluate(z3var, model_completion=True), model=model
            )
            variable = self.context["z3_to_variable"][var_name]
            dtype = DataType.build(variable.type)
            logger.info(
                f"Variable {var_name} with Z3 value {concrete} and data type {dtype}"
            )
            if dtype.is_type(*DataType.TEMPORAL_TYPES):
                concrete = datetime.fromtimestamp(concrete)
            if dtype.is_type(*DataType.TEXT_TYPES) and concrete == "":
                logger.info(
                    f"Variable {var_name} get empty concrete {concrete} skipping"
                )
                continue

            result[var_name] = concrete
        if self.verbose:
            logger.info(result)
        return result

    def _z3_to_python(self, val, model: Optional[z3.ModelRef] = None):
        if isinstance(val.sort(), z3.DatatypeSortRef):
            opt = OptionTypeRegistry.from_sort(val.sort())
            is_null = model.evaluate(opt.is_NULL(val), model_completion=True)
            is_some = model.evaluate(opt.is_Some(val), model_completion=True)
            if is_null:
                return None
            if is_some:
                inner_val = model.evaluate(opt.value(val), model_completion=True)
                return self._z3_to_python(inner_val, model=model)
            else:
                raise RuntimeError(f"Invalid option value: {val}")

        # ---------- Int ----------
        if z3.is_int_value(val):
            return val.as_long()

        # ---------- Real ----------
        if z3.is_rational_value(val):
            s = val.as_decimal(20)
            return float(s.replace("?", ""))

        # ---------- String ----------
        if z3.is_string_value(val):
            return val.as_string()

        if z3.is_true(val):
            return True
        if z3.is_false(val):
            return False

        return str(val)


# # exprs = exp.And(this= exp.And(
# #     this= exp.GT(this = exp.Column(this="age", table="T1", datatype="INT"), expression=exp.Literal(this = 30, datatype="INT")),
# #     expression= exp.LT(this = exp.Column(this="age", table="T1", datatype="INT"), expression=exp.Literal(this = 40, datatype="INT"))
# # ), expression= exp.NEQ(this = exp.Column(this="name", table="T1", datatype="TEXT"), expression=exp.Literal(this = "Alice", datatype="TEXT")))

# # exprs = exp.And(this = exp.Column(this="age", table="T1", datatype="INT") > exp.Literal(this = 30, datatype="INT"), expression= exp.LT(this = exp.Column(this="age", table="T1", datatype="INT"), expression=exp.Literal(this = 40, datatype="INT")))

# exprs = exp.Like(this = exp.Column(this="name", table="T1", datatype="TEXT"), expression=exp.Literal(this = "A%", datatype="TEXT"))

# for _ in range(5):
#     solver = SMTSolver()
#     exprs = exp.EQ(this = exp.Column(this="name", table="T1", datatype="TEXT"), expression=exp.Column(this = "Alice", table = "T2", datatype="TEXT"))

#     # # # exprs = exp.Not(this = exp.Is(this = exp.Column(this="age", table="T1", datatype="STRING"), expression=exp.Literal(this = None, datatype="STRING")))

#     smt_expr = solver._to_z3_expr(exprs)
#     # # print(solver.context)
#     # print(smt_expr)
#     solver.add(smt_expr)
#     print(solver.solve())
#     # # # print(solver.solver.check())
#     # # # print(solver.solver.model())

#     # # print(datetime.now().timestamp())
