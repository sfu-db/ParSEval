from __future__ import annotations
from abc import ABC, abstractmethod
import typing as t
from sqlglot import exp
from itertools import chain, product

from enum import Enum, auto
class AutoName(Enum):
    def _generate_next_value_(name, _start, _count, _last_values):
        return name


E = t.TypeVar("E")

class UExpr(exp.Expression):
    key = "uexpr"
    def __mul__(self, rhs: 'UExpr') -> 'UExpr':

        if isinstance(self, USummation) and isinstance(rhs, USummation):
            return USummation(expressions = [left * right for left, right in product(self.expressions, rhs.expressions)])
        elif isinstance(self, USummation):
            return USummation(expressions = [expr * rhs for expr in self.expressions])
        elif isinstance(rhs, USummation):
            return USummation(expressions = [self * expr for expr in rhs.expressions])
        elif isinstance(self, UAdd):
            return UAdd(this = self.this * rhs, expression = self.expression * rhs)
        elif isinstance(rhs, UAdd):
            return UAdd(this = self * rhs.this, expression = self * rhs.expression)
        else:
            return UMul(this = self, expression = rhs)

    def __add__(self, rhs: 'UExpr'):
        if isinstance(self, USummation) and isinstance(rhs, USummation):
            return USummation(expressions =  chain(self.expressions, rhs.expressions))
        elif isinstance(self, USummation):
            return USummation(expressions = chain(self.expressions, rhs))
        elif isinstance(rhs, USummation):
            return USummation(expressions = chain(self, rhs.expressions))
        return UAdd(this = self, expression = rhs)

    def sql(self, dialect: exp.DialectType = None, **opts) -> str:
        if isinstance(self, USummation):
            # for expr in list(self.expressions):
            #     print(expr)
            return f"∑ ({ ', '.join([expr.sql(dialect) for expr in self.expressions])})"
        elif isinstance(self, Relation):            
            return f"⟦ R_{self.args.get('table')}({self.this}) ⟧"
        elif isinstance(self, UConnector):
            mapping = {
                'umul' : 'x',
                'uadd': '+'
            }
            return f"{self.this} {mapping[self.key]} {self.expression}"
        elif isinstance(self, UPredicate):
            return f"|{self.this}|"
        elif isinstance(self, UTuple):
            return f"Tuple({self.this})"
        else:
            return f'{self.this}'

class UTuple(UExpr):
    arg_types = {"this": False, "expressions": True}
    def get(self, index):
        return self.expressions[index]
    @property
    def count(self) -> Relation:
        return self.args.get('count')
    def __mul__(self, rhs):
        if isinstance(rhs, UTuple):
            return UTuple(this = f'{self.this} x {rhs.this}', expressions = [*self.expressions, *rhs.expressions], count = UMul(this = self.count, expression = rhs.count))
        else:
            return UTuple(this = self.this, expressions = [*self.expressions, rhs])

    def __add__(self, rhs):
        if isinstance(rhs, UTuple):
            return UTuple(this = f'{self.this} + {rhs.this}',expressions = [*self.expressions, *rhs.expressions])
        else:
            return UTuple(this = self.this, expressions = [*self.expressions, rhs])
    
    def __len__(self) -> int:
        return len(self.expressions)
    
    def __str__(self):
        return f'Tuple({self.this})'

class USummation(UExpr):
    arg_types = {"expressions": True, 'upper': False}

class UConnector(UExpr):
    arg_types = {"this": True, "expression": True}

    @property
    def left(self):
        return self.this
    @property
    def right(self):
        return self.expression
    
class UAdd(UConnector):
    ...

class UMul(UConnector):
    ...

class USquash(UExpr):
    ...

class Relation(UExpr):
    arg_types = {"this" : False, "db": False, "t" : False, "table": False}

class UPredicate(UExpr):
    ...

#     def __str__(self):
#         return f"⟦ {self.this}({self.args.get('t')}) ⟧"
    
#     @property
#     def t(self):
#         return self.expression

#     def print_uexpr(self, **kwargs):
#         return f"⟦ {self.this}({self.args.get('t')}) ⟧"

#     def to_uexpr(self):
#         return str(self)
    
#     def evaluate(self, executor, **kwargs):
#         return executor.execute_relation(self, **kwargs)

def get_tuples(expr: UExpr) -> t.Generator[t.Tuple[exp.Column]]:
    if isinstance(expr, USummation):
        for e in expr.expressions:
            yield from get_tuples(e)
    else:
        tuples = []
        for relation in expr.find_all(Relation):
            ttt = relation.args.get('t').expressions
            tuples.extend(ttt)
        yield tuples


def get_columnref(expr: exp.Column):
    
    return int(expr.args.get('ref'))


def substitute_column(expr, row):
    def sutstitute(node):
        if isinstance(node, exp.Column):
            return row.get(get_columnref(node))
        return node
    return expr.transform(sutstitute)


def update_tuple(expr: UExpr):
    if isinstance(expr, USummation):
        expr.set('expressions', list(map(lambda x : update_tuple(x), expr.expressions)))
        # expr.expressions = list(map(lambda x : update_tuple(x), expr.expressions))
        return expr
    else:
        tuples = list(expr.find_all(UTuple))
        if len(tuples) < 2:
            return expr
        new_tuples = []
        for tup in tuples:
            new_tuples.extend(tup.expressions)
        def substitute_tuple(node):
            if isinstance(node, UTuple):
                return None
            return node
        expr.transform(substitute_tuple)
        expr.args['t'] = new_tuples
        return expr


