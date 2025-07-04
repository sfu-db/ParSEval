import logging, z3
from typing import List, Dict, Tuple
from datetime import date, datetime
from contextlib import contextmanager

logger = logging.getLogger('src.solver')

z3.set_option(html_mode=False)
z3.set_option(rational_to_decimal = True)
z3.set_option(precision = 32)
z3.set_option(max_width = 21049)
z3.set_option(max_args = 100)

@contextmanager
def checkpoint(z3solver):
    z3solver.push()
    yield z3solver
    z3solver.pop()

# z3.Implies
LABELED_NULL = {
    'INT' : 6789,
    'REAL' : 0.6789,
    'STRING' : 'NULL',
    'BOOLEAN' : 'NULL',
    'DATETIME' : datetime(1970, 1, 1, 0, 0, 0),
    'DATE' : date(1970, 1, 1),
}

class Solver(object):
    options = {}

    def _build_smt_expr(self, paths: List):
        symbols = z3.z3util.get_vars(*paths)
        return z3.And(paths)
    def solve(self, paths):
        solver = z3.Solver()
        solver.add(*paths)
        if solver.check() == z3.sat:
            return solver.model()

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

    def _find_model(self, paths: List, extend_paths):
        s = z3.Solver()

        
        s.add(z3.And(paths))
        s.add(z3.And(extend_paths))

        symbols = set()
        for f in [*paths, *extend_paths]:
            symbols.update(z3.z3util.get_vars(f))
        
        for symbol in symbols:
            if symbol.sort().name() == 'String':
                s.add(z3.Length(symbol) > 0)
        

        if s.check() == z3.unsat:
            return 'No Solutions', {}
        elif s.check() == z3.unknown:
            return 'Gave up', {}
        assert s.check() == z3.sat
        m = s.model()
        return 'sat', {d.name(): self._to_concrete(m[d]) for d in m.decls()}

    def _find_model2(self, paths: Dict):
        s = z3.Solver()
        unsolved = set()

        if 'DB' in paths:
            s.add(*paths.get('DB'))
        
        if 'POSITIVE' in paths:
            for c in paths.get('POSITIVE'):
                s.add(c)

        if s.check() == z3.unsat:
            return 'No Solutions', {}, None
        elif s.check() == z3.unknown:
            return 'Gave up', {}, None
        
        if 'NEGATIVE' in paths:
            for identifier, c in paths.get('NEGATIVE').items():
                s.push()
                s.add(c)
                if s.check() != z3.sat:
                    s.pop()
                    unsolved.add(identifier)

        assert s.check() == z3.sat
        m = s.model()
        return 'sat', {d.name(): self._to_concrete(m[d]) for d in m.decls()}, unsolved
    
        # with checkpoint(s):
        #     if self.z3.check() != z3.sat:
        #         return {}
        #     m = self.z3.model()
        #     solutions = {d.name(): m[d] for d in m.decls()}
        #     my_args = {k: solutions.get(k, None) for k in self.fn_args}
        
        # return my_args


    def _build_expr(self, extend_vars, extend_queries):
        ...

