from __future__ import annotations
from typing import List, Dict, Any, Tuple, TYPE_CHECKING, Optional, Set, Union
from src.expression.visitors.z3_visitor import Z3Visitor

if TYPE_CHECKING:
    from src.expression.symbol import Expr
import z3
from contextlib import contextmanager

@contextmanager
def checkpoint(z3solver):
    z3solver.push()
    yield z3solver
    z3solver.pop()

class Solver:
    def __init__(self, timeout: int = 3000, debug = False, **kwargs):
        self.solver = z3.Solver()
        self.timeout = timeout
        self.debug = debug
        self.solver.set('timeout', self.timeout)

        self.expressions: List[z3.ExprRef] = []
        self.last_model: Optional[z3.ModelRef] = None
        self.variable_mapping: Dict[str, z3.ExprRef] = {}

    
    def add(self, expr: Union[Expr, List[Expr]]):
        ''''''
        if not isinstance(expr, list):
            expr = [expr]
        for e in expr:
            self._add_expr(e)

    def _add_expr(self, expr: Expr):
        visitor = Z3Visitor(self.variable_mapping)
        e = expr.accept(visitor)
        self.expressions.append(e)
        self.solver.add(e)

    def check(self) -> bool:
        result = self.solver.check()
        if result == z3.sat:
            self.last_model = self.solver.model()
            return True
        return False

    def model(self):
        if self.last_model is None:
            return None
        
        return {d.name(): self._to_concrete(self.last_model[d]) for d in self.last_model.decls()}
        


    def preprocess(self, paths: Dict[str, List]):
        ...

    def to_smt_expr(self, paths: Dict[str, List]) -> z3.ExprRef:
        ...

    def evaluate(self, expressions, target_symbols: Optional[List] = None):
        ...

    def check_sat(self, smt_exprs: List) -> bool:
        ...

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
        return concrete

