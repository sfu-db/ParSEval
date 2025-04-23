from __future__ import annotations
from sqlglot import exp
from typing import List, Dict, Any, Tuple, TYPE_CHECKING, Optional, Set
from src.expr import Symbol
if TYPE_CHECKING:    
    from src.context import Context
import z3
from contextlib import contextmanager

@contextmanager
def checkpoint(z3solver):
    z3solver.push()
    yield z3solver
    z3solver.pop()




class Solver:
    def __init__(self, context: Context, **kwargs):
        self.context = context

        self.solver = z3.Solver() ### we could switch to different solvers in the future.


    def preprocess(self, paths: Dict[str, List[Symbol]]):
        ...

    def to_smt_expr(self, paths: Dict[str, List[Symbol]]) -> z3.ExprRef:
        ...

    def evaluate(self, expressions, target_symbols: Optional[List] = None):
        ...

    def check_sat(self, smt_exprs: List[Symbol]) -> bool:
        ...

    def find_model(self, paths: Dict[str, List[Symbol]]) -> Tuple[str, Dict[str, Any], Set[str]]:
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

