import logging, z3
from typing import List, Dict, Tuple, Optional
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
    '''
        A SMT Solver for symbolic expressions.
    '''
    options = {}

    def preprocess(self, expressions):
        all_vars = set()
        

    def evaluate(self, expressions, target_symbols: Optional[List] = None):
        """
        Evaluate symbolic expressions to find concrete values for target symbols.
        
        Args:
            expressions: A list of symbolic expressions or a single expression
            target_symbols: Specific symbols to evaluate (if None, evaluate all)
            **options: Additional options for the solver
                - 'existing_values': Dictionary of existing symbol values to preserve
                - 'timeout': Maximum time in milliseconds for solving (optional)
        
        Returns:
            A tuple (result, model) where:
            - result: 'sat' if a solution was found, 'unsat' if no solution exists,
                    or 'unknown' if the solver couldn't determine satisfiability
            - model: A dictionary mapping variable names to concrete values
        """
        if not isinstance(expressions, list):
            expressions = [expressions]
                
        if target_symbols is None:
            target_symbols = set()
            for expr in expressions:
                target_symbols.update(z3.z3util.get_vars(expr))
        
        s = z3.Solver()
        for expr in expressions:
            s.add(expr)
        result = s.check()
        if result == z3.sat:
            m = s.model()
            concrete_model = {}
            for var in target_symbols:
                if var in m.decls():
                    concrete_model[str(var)] = self._to_concrete(m[var])
            return 'sat', concrete_model
        else:
            return 'unsat', {}

    def check_sat(self, paths) -> bool:
        solver = z3.Solver()
        solver.add(*paths)
        return solver.check() == z3.sat

        
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
   


    def _build_expr(self, extend_vars, extend_queries):
        ...

