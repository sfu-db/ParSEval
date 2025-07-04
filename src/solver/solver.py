from __future__ import annotations
from typing import List, Dict, Any, Tuple, TYPE_CHECKING, Optional, Set, Union
from src.expression.visitors.z3_visitor import Z3Visitor, LABELED_NULL
from src.expression.types import DataType
from collections import deque

if TYPE_CHECKING:
    from src.expression.symbol import Expr, Variable
import z3
from contextlib import contextmanager

@contextmanager
def checkpoint(z3solver):
    z3solver.push()
    yield z3solver
    z3solver.pop()

z3.set_option(html_mode=False)
z3.set_option(rational_to_decimal=True)
z3.set_option(precision=32)
z3.set_option(max_width=21049)
z3.set_option(max_args=100)


class Solver:
    def __init__(self, target_vars: List[Variable], timeout: int = 3000, debug = True, **kwargs):
        self.solver = z3.Solver()
        self.timeout = timeout
        self.debug = debug
        self.solver.set('timeout', self.timeout)
        

        self.coverage_expressions: List[z3.ExprRef] = [] ## will solve constraints in coverage_expression one by one
        self.conditional_expressions: List[z3.ExprRef] = []

        self.last_model: Optional[z3.ModelRef] = None
        self.symbol_mapping: Dict[str, z3.ExprRef] = {}
        self.variable_mapping: Dict[str, Variable] = {}
        self.target_vars: List[Variable] = target_vars


    def add_conditional(self, exprs: Union[Expr, List[Expr]])-> bool:
        '''
            Add conditional constraints(e.g. DB constraints) if its variables intersect with coverage expression.
            The solver will return SAT if and only if both conditional and coverage expression can be satisfied 
        '''
        if not isinstance(exprs, list):
            exprs = [exprs]
        
        variable_mapping = {}
        symbol_mapping = {}
        smt_exprs = []
        for e in exprs:
            visitor = Z3Visitor(variable_mapping, symbol_mapping)
            e = e.accept(visitor)
            used_symbols = set(symbol_mapping.values())
            if used_symbols.intersection(self.symbol_mapping.values()):
                smt_exprs.append(e)

        self.variable_mapping.update(variable_mapping)
        self.symbol_mapping.update(symbol_mapping)
        self.conditional_expressions.extend(smt_exprs)
        return len(smt_exprs) > 0


    def append(self, expr: Union[Expr, List[Expr]]):
        ''''''
        if not isinstance(expr, list):
            expr = [expr]
        visitor = Z3Visitor(self.variable_mapping, self.symbol_mapping)
        for e in expr:
            smt_expr = e.accept(visitor)
            self.coverage_expressions.append(smt_expr)

    def appendleft(self, expr: Union[Expr, List[Expr]]):        
        if not isinstance(expr, list):
            expr = [expr]
        visitor = Z3Visitor(self.variable_mapping, self.symbol_mapping)
        for e in expr:
            
            smt_expr = e.accept(visitor)
            if "IS NULL" in str(e):
                import logging 
                logging.info(f"IS NULL CONSTRAING: {e}, smt: {smt_expr}")
            self.coverage_expressions.insert(0, smt_expr)        
                
    def check(self) -> bool:
        sat = True
        ## add conditional format constraitns 
        self.conditional_expressions.extend(self.preprocess())
        for smt_expr in [*self.conditional_expressions, *self.coverage_expressions]:
            self.solver.push()
            self.solver.add(smt_expr)
            if self.solver.check() == z3.sat:
                self.last_model = self.solver.model()
            else:
                self.solver.pop(num = 1)
                sat = False
        self.log_smt()
        return sat
    
    def model(self):
        if self.last_model is None:
            return {}
        return {d.name(): self._to_concrete(self.last_model[d]) for d in self.last_model.decls()}
        
    def preprocess(self) -> List[z3.ExprRef]:
        smt_exprs = []
        for v_name, symbol in self.symbol_mapping.items():
                # smt_exprs.append(self.ensure_printable(symbol))
            if self.variable_mapping[v_name].dtype.is_type(*DataType.TEMPORAL_TYPES):
                smt_exprs.append(self.ensure_datetime(symbol))
                
            if isinstance(symbol.sort(), z3.SeqSortRef):
                smt_exprs.append(z3.Length(symbol) > 0)
            
        visitor = Z3Visitor(self.variable_mapping, self.symbol_mapping)
        for v_name, variable in self.variable_mapping.items():
            if variable in self.target_vars:
                continue
            e = variable == variable.value
            smt_exprs.append(e.accept(visitor))
        
        return smt_exprs
    def ensure_datetime(self, s):
        # Extract substrings
        year    = z3.SubString(s, 0, 4)
        month   = z3.SubString(s, 5, 2)
        day     = z3.SubString(s, 8, 2)
        hour    = z3.SubString(s, 11, 2)
        minute  = z3.SubString(s, 14, 2)
        second  = z3.SubString(s, 17, 2)
        ms      = z3.SubString(s, 20, 3)

        # Convert to integers
        year_i    = z3.StrToInt(year)
        month_i   = z3.StrToInt(month)
        day_i     = z3.StrToInt(day)
        hour_i    = z3.StrToInt(hour)
        minute_i  = z3.StrToInt(minute)
        second_i  = z3.StrToInt(second)
        ms_i      = z3.StrToInt(ms)

        digit = z3.Range('0', '9')
        two_digits = z3.Concat(digit, digit)
        three_digits = z3.Concat(digit, digit, digit)
        four_digits = z3.Concat(digit, digit, digit, digit)

        # YYYY/MM/DD HH:MM:SS.mmm
        datetime_re = z3.Concat(
            four_digits, z3.Re('-'),
            two_digits, z3.Re('-'),
            two_digits, z3.Re(' '),
            two_digits, z3.Re(':'),
            two_digits, z3.Re(':'),
            two_digits, z3.Re('.'),
            three_digits
        )
        smt_exprs = [year_i >= 1900, year_i <= 2100, month_i >= 1,  month_i <= 12
                    ,hour_i >= 0,    hour_i <= 23,
                    day_i >= 1,     day_i <= 31,
                     minute_i >= 0,  minute_i <= 59,
                     second_i >= 0,  second_i <= 59 ,
                      ms_i >= 0,      ms_i <= 999,
                      z3.InRe(s, datetime_re)]
        
        return z3.And(*smt_exprs)

        
    def ensure_printable(self, s):
        # ASCII printable range: from code point 32 (' ') to 126 ('~')
        ascii_printable = z3.Range(chr(32), chr(126))

        # Require the string to be made of printable ASCII chars only
        ascii_printable_word = z3.Plus(ascii_printable)  # allows zero or more
        # Constraint: s ∈ ascii_printable_word
        constraint =z3.InRe(s, ascii_printable_word)
        return constraint
        
    def log_smt(self):
        if self.debug:
            with open("tests/db/smt.txt", 'a') as fp:
                fp.write("(set-logic ALL)\n")
                # Collect declarations
                for v_name, symbol in self.symbol_mapping.items():
                    fp.write(f"(declare-fun {symbol.decl().name()} () {symbol.sort()})\n") 
                
                # Add assertions
                for smt_expr in [*self.conditional_expressions, *self.coverage_expressions]:
                    fp.write(f"(assert {smt_expr.sexpr()})\n")
                fp.write("(check-sat)\n(get-model)\n")
                fp.write("**" * 20 + '\n')

    

    def find_model(self, paths: Dict[str, List]) -> Tuple[str, Dict[str, Any], Set[str]]:
        unsolved = set()

        # with checkpoint(self.solver) as s:
        if 'DB' in paths:
            self.solver.add(*paths.get('DB'))

        if 'POSITIVE' in paths:
            for c in paths.get('POSITIVE'):
                self.solver.add(c)

        if self.solver.check() == z3.unsat:
            return 'No Solutions', {}, None
        elif self.solver.check() == z3.unknown:
            return 'Gave up', {}, None

        if 'NEGATIVE' in paths:
            for identifier, c in paths.get('NEGATIVE').items():
                self.solver.push()
                self.solver.add(c)
                if self.solver.check() != z3.sat:
                    self.solver.pop()
                    unsolved.add(identifier)

        assert self.solver.check() == z3.sat
        m = self.solver.model()
        return 'sat', {d.name(): self._to_concrete(m[d]) for d in m.decls()}, unsolved


    def _to_concrete(self, z3val):
        if isinstance(z3val, z3.FuncInterp):
            return self._to_concrete(z3val.else_value())
        sort = z3val.sort().name()
        concrete = None
        if sort == 'Int':
            concrete = z3val.as_long()
        elif sort == 'Real':
            concrete = z3val.as_decimal(prec= 32)
            concrete = concrete[:-1] if concrete.endswith('?') else concrete
            concrete = float(concrete)
        elif sort == 'Bool':
            concrete = bool(z3val)
        elif sort == 'String':
            concrete = z3val.as_string()
        else:
            raise RuntimeError(f'Cannot interpret {z3val}')
        if concrete in LABELED_NULL.values():
            return None
        return concrete

