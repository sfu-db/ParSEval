from __future__ import annotations
import typing as t
from sqlglot import exp, Dialect
import z3
class Is_Null(exp.Unary, exp.Predicate):
    def __str__(self):
        return f"{self.this} IS NULL"

class Strftime(exp.Func):
    arg_types = {"this": True, "format": True, "culture": False}

class InStr(exp.Func):
    arg_types = {"this": True, "substring": True, "start": False}

class Julianday(exp.Func):
    arg_types = {"this": False, "expressions": False}

class Implies(exp.Condition):
    arg_types = {"this": True, "expression": True}

#######################################################################
##                   Definition of Query Plan                       ##
#######################################################################

class Step(exp.Expression):
    def sql(self, dialect: exp.DialectType = None, **opts) -> str:
        return '\n'.join(self._to_s(''))
    @property
    def left(self) -> Step:
        return self.this
    @property
    def type_name(self) -> str:
        return self.__class__.__name__

    def i(self):
        return self.text('id')

class Scan(Step):
    arg_types = {
        "this" : False,
        "table" : True,
    }
    @property
    def table(self) -> str:
        return self.text('table')
    
    def _to_s(self, indent: str) -> t.List[str]:
        return [f"{indent}{self.type_name}({self.text('table')})"]

class Project(Step):
    arg_types = {
        "this" : True,
        "expressions": True
    }
    @property
    def projections(self) -> t.List[exp.Expression]:
        return self.expressions
        
    def _to_s(self, indent: str) -> t.List[str]:
        nested = indent + '  '
        s = [f"{indent}{self.type_name}({', '.join([str(proj) for proj in self.projections])})"]
        s.extend(self.left._to_s(nested))
        return s
    
class Filter(Step):
    arg_types = {
        "this" : True,
        "condition" : True
    }
    @property
    def condition(self):
        return self.args.get('condition')
    
    def _to_s(self, indent: str) -> t.List[str]:
        nested = indent + '  '
        s = [f"{indent}{self.type_name}(condition=[{self.condition}])"]
        s.extend(self.left._to_s(nested))
        return s

class Scalar(Step):
    arg_types = {
        "this" : True
    }
    def __repr__(self):
        return self._to_s('')
    
    def sql(self, dialect = None, **opts):
        return self.key
    
    def _to_s(self, indent: str) -> t.List[str]:
        print('((8***))' * 10)
        nested = indent + '  '
        s = [f"{indent}{self.type_name}(query=[{self.this.key}])"]
        # s.extend(self.left._to_s(nested))
        return s



class Aggregate(Step):
    arg_types = {
        "this" : True,
        "groupby" : True,
        "agg_funcs": False
    }
    @property
    def groupby(self):
        return self.args.get('groupby')
    @property
    def agg_funcs(self):
        return self.args.get('agg_funcs')
    
    def _to_s(self, indent: str) -> t.List[str]:
        nested = indent + "  "
        expr = [*(c.sql() for c in self.groupby), *(f.sql() for f in self.agg_funcs)]
        s = [f"{indent}{self.type_name}({', '.join(expr)})"]
        s.extend(self.left._to_s(indent= nested))
        return s

class Values(Step):
    arg_types = {
        "this": False,
        "values": False
    }
    def _to_s(self, indent: str) -> t.List[str]:
        s = [f"{indent}{self.type_name}({', '.join([str(v) for v in self.values])})"]
        return s
class Sort(Step):
    arg_types = {
        "this" : True,
        "dir" : True,
        "offset": True,
        "limit": False
    }
    @property
    def offset(self):
        return self.args.get('offset')
    @property
    def limit(self):
        return self.args.get('limit')

    def _to_s(self, indent: str) -> t.List[str]:
        nested = indent + '  '
        orderby = [col['column'] for col in self.args.get('sort')]
        s = [f"{indent}{self.type_name}(sort = {orderby}, dir = {self.args.get('dir')}, offset = {self.args.get('offset')}, limit = {self.args.get('limit')})"]
        s.extend(self.left._to_s(nested))
        return s

class Join(Step):
    arg_types = {
        "this" : True,
        "expression": True,
        "kind": True,
        "conditon" : True
    }
    @property
    def right(self) -> Step:
        return self.expression
    @property
    def condition(self) -> exp.Expression:
        return self.args.get('condition')
    @property
    def kind(self):
        return self.args.get('joinType')
    
    def _to_s(self, indent: str) -> t.List[str]:
        nested = indent + '  '
        s = [f'{indent}{self.type_name}(condition=[{self.condition}], join_kind={self.kind})']
        s.extend(self.left._to_s(nested))
        s.extend( self.right._to_s(nested))
        return s
    
class Union(Step):
    arg_types = {
        "this" : True,
        "expression": True,
        "all": True
    }
    @property
    def right(self) -> Step:
        return self.expression
    def _to_s(self, indent: str) -> t.List[str]:
        nested = indent + '  '
        s = [f'{indent}{self.type_name}(all={self.args.get("all")})']
        s.extend(self.left._to_s(nested))
        s.extend( self.right._to_s(nested))
        return s

class Intersect(Union):
    ...

class Minus(Union):
    ...
class Correlate(Step):
    ...

#######################################################################
##                   Definition of UExpression                       ##
#######################################################################


class _UExpression(exp.Expression):
    arg_types = {'this': True, 'expression': False}
    def __repr__(self):
        return str(self)

class UExpression(_UExpression):
    def to_smt(self):
        if isinstance(self.this, UExpression):
            return self.this.to_smt()
        return self.this
    
    def __mul__(self, other):
        if isinstance(other, int) and other == 1:
            return self
        if isinstance(other, UExpression):
            return UMul(this = self, expression = other)
        raise ValueError(f'cannot mul UExpression with other types: {type(other)}')
    def __add__(self, other):
        if isinstance(other, UExpression):
            return UAdd(this = self, expression = other)
        if other == 0:
            return self
        raise ValueError(f'cannot add UExpression with other types: {type(other)}')
    __rmul__ = __mul__

    __radd__ = __add__
    
    # def sql(self, dialect = None, **opts):

    #     return super().sql(dialect, **opts)
    
class UBinary(UExpression):
    arg_types = {"this": True, "expression": True}

    @property
    def left(self) -> UExpression:
        return self.this

    @property
    def right(self) -> UExpression:
        return self.expression


class UAdd(UBinary):
    def __str__(self):
        return f"{self.left} + {self.right}"

    def to_smt(self):
        return self.left.to_smt() + self.right.to_smt()

class UMul(UBinary):
    def __str__(self):
        return f"{self.left} * {self.right}"

    def to_smt(self):
        return self.left.to_smt() * self.right.to_smt()

class Term(UExpression):
    arg_types = {"this": True, "t": False}

    def __str__(self):
        return str(self.this)
    def to_smt(self):
        return self.this

class Summation(UExpression):
    arg_types = {'expressions': True}

    def __str__(self):
        return f"∑({self.expressions})"
    
class Relation(UExpression):
    arg_types = {"this" : True, "db": False, "expressions" : False, "table": False}

    def __str__(self):
        return f"R({self.this})"

class Row(UExpression):
    arg_types = {'expressions' : True, 'multiplicity': True}

    @property
    def multiplicity(self):
        return self.args.get('multiplicity')
    def __str__(self):
        s = ', '.join([str(e) for e in self.expressions])
        return f"{self.multiplicity} :Row({s})"
    
    def __getitem__(self, other):
        return self.expressions[other]

    def __mul__(self, other):
        c = [*self.expressions, *other.expressions]
        multiplicity = self.multiplicity * other.multiplicity
        return Row(expressions = c, multiplicity = multiplicity)

class Pred(UExpression):
    '''
        Pred is one specific ``Predicate'' encountered during the uexpression execution.
    '''
    arg_types = {'this': True, 'result': False, 't': True}

    @property
    def result(self):
        return self.args.get('result')
    @property
    def t(self):
        return self.args.get('t')
    
    def to_smt(self):
        return self.this.oneif()
    # z3.If(self.this.expr, 1, 0)
    
    def __mul__(self, other):
        if isinstance(other, int) and other == 1:
            return self
        if isinstance(other, Pred):
            t = self.t if other.t is None else self.t * other.t
            return Pred(this = self.this * other.this , result = self.result * other.result, t = t)
        raise ValueError(f'cannot mul Pred with other types: {type(other)}')

    def __add__(self, other):
        if isinstance(other, Pred):
            t = t = self.t if other.t is None else self.t + other.t
            return Pred(this = self.this + other.this , result = self.result + other.result, t = t)
        if other == 0:
            return self
        raise ValueError(f'cannot add Pred with other types: {type(other)}')

    def __lt__(self, other):
        return self.result < other

    def __gt__(self, other):
        return self.result > other

    def __radd__(self, other):
        return self.__add__(other)

    def __eq__(self, other):
        if isinstance(other, Pred):
            res = self.result == other.result and self.this.symbolic_eq(other.this)
            return res
        else:
            return False

    def __repr__(self):
        return "⟦UExpr: %s -> %s⟧" % (self.this, self.args.get('result'))

    def __str__(self):
        return f"⟦{self.this}⟧"
