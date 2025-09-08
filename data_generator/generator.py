
from __future__ import annotations
from typing import Any, TypeVar, Union, Set, Tuple, Dict, List, Optional, Generator, AnyStr
import z3, re, subprocess, tempfile, os, logging, random
from sqlglot import exp
from itertools import combinations
from functools import reduce
import calendar, datetime
from collections import deque, defaultdict
from parseval.symbol import NULL, Term
from parseval.query import uexpr
from parseval.exceptions import UnSupportError
logger = logging.getLogger('app')

z3.set_option(html_mode=False)
z3.set_option(rational_to_decimal = True)
z3.set_option(precision = 4)
z3.set_option(max_width = 21049)
z3.set_option(max_args = 100)

# z3.Implies
LABELED_NULL = {
    'INT' : 6789,
    'REAL' : 0.6789,
    'STRING' : 'NULL',
    'BOOLEAN' : 'NULL',
    'DATETIME' : datetime.datetime(1970, 1, 1, 0, 0, 0),
    'DATE' : datetime.date(1970, 1, 1),
}
def fix_day(year, month, day):
    _, last_day_of_month = calendar.monthrange(year, month)
    if day > last_day_of_month:
        day = last_day_of_month
    return day

def convert_z3ref_to_concrete(val):
    if isinstance(val, z3.FuncInterp):
        return convert_z3ref_to_concrete(val.else_value())
    val_s = str(val.sort())

    # if z3.is_arith(val):
    #     return None

    if val_s == 'Int':
        if isinstance(val, z3.IntNumRef):
            return int(val.as_long())
        else:
            return None
    elif val_s == 'Real':
        if isinstance(val, z3.RatNumRef):
            v = val.as_decimal(prec= 8)
            v = v[:-1] if v.endswith('?') else v
            return round(float(v), 8)
        else:
            return None
        
        
    elif val_s == 'Boolean':
        v = str(val)
        if '(' in v:
            v = v[v.index('(') + 1 : v.index(')')]
        if v == 'true':
            return True
        elif v == 'flase':
            return False
        return NULL()
    elif val_s == 'Date':
        v = str(val)            
        v = v[v.index('(') + 1 : v.index(')')]
        concrete =[int(c) for c in v.split(',')]
        concrete[2] = fix_day(concrete[0], concrete[1], concrete[2])
        r = datetime.date(*concrete)
        return r
    elif val_s == 'Datetime':
        v = str(val)
        v = v[v.index('(') + 1 : v.index(')')]
        concrete =[int(c) for c in v.split(',')]
        concrete[2] = fix_day(concrete[0], concrete[1], concrete[2])
        return datetime.datetime(*concrete)
    elif val_s == 'String':
        val = val.as_string()
        val = val[1:-1] if '"' in val else val 
        v = val
        v = v if str(v) != '' else 'EMPTY_KEY'
        return v
    raise RuntimeError(f'Cannot interpret {val}')
def get_fk_from_column(expression: exp.ForeignKey):
    
    dep_table = expression.args.get('reference').find(exp.Table)
    dep_table_name = dep_table.name
    dep_table_column_name = dep_table.expressions[0].name
    return expression.expressions[0].this, dep_table_name, dep_table_column_name
import string
def like(symbol, other):
    constraints = []        
    characters = string.ascii_letters + string.digits  # You can include other characters as needed
    start = 0
    base = ''
    for o in other:
        if o == '%':
            k = random.randint(1, 4)
            random_string = ''.join(random.choices(characters, k = k))
            if len(base):
                constraints.append(z3.SubString(symbol, start, len(base)) == str(base))
                start = start + len(base)
            rz = z3.SubString(symbol, start, k)
            constraints.append(rz == random_string)
            start = start + k
            base = ''
        elif o == '_':
            if len(base):
                constraints.append(z3.SubString(symbol, start, len(base)) == str(base))
                start = start + len(base)
            constraints.append(z3.SubString(symbol, start, 1) == random.choice(characters))
            start = start + 1
            base = ''
        else:
            base += o
    if not constraints:
        constraints.append(z3.Contains(symbol, other))    
    return z3.And(constraints)

def cast(symbol, from_type, to_type):
    if from_type.is_type(to_type):
        return symbol
    elif from_type.is_type(*exp.DataType.TEXT_TYPES) and to_type.is_type(*exp.DataType.INTEGER_TYPES):
        return z3.StrToInt(symbol)
    elif from_type.is_type(*exp.DataType.INTEGER_TYPES) and to_type.is_type(*exp.DataType.REAL_TYPES):
        return z3.ToReal(symbol)
    elif from_type.is_type(*exp.DataType.INTEGER_TYPES) and to_type.is_type(*exp.DataType.TEXT_TYPES):
        return z3.IntToStr(symbol)    
    raise UnSupportError(f'could not cast {symbol} from {from_type} to {to_type}')
   

class Generator:
    def __init__(self, ctx = None) -> None:
        self.ctx = ctx
        self.multipliticy_func = z3.Function("Relation", z3.StringSort(ctx = self.ctx), z3.StringSort(ctx = self.ctx), z3.IntSort(ctx = self.ctx))
        self.declares = {
            'tuples' : defaultdict(set),
            'terms': {},
            'relations' : set(),
            'multipliticy': {},
            'uniques' : set()
        }
        self.context = set()

    def declare_context_constraints(self, term):
        if term.key == 'string':
            self.context.add(z3.Length(term.symbol) != 0)


    def solve(self, paths):
        solver = z3.Solver(ctx = self.ctx)
        solver.add(z3.And(paths))
        ## make sure multipliticy of a tuple t in relation is GTE 0
        t = z3.String('t', ctx=self.ctx)
        for table_name in self.declares['relations']:
            solver.add(z3.ForAll(t, self.multipliticy_func(t, z3.StringVal(table_name, ctx = self.ctx)) >= 0))
        solver.add(*self.context)

        # print(solver.sexpr())
        if solver.check() == z3.sat:
            model = solver.model()
            # print(model)
            return self.to_concretes(model)
        else:
            print(f'could not solve uexpression')
            return {}
    def to_concretes(self, model: z3.Solver.model):
        results = {}
        for tuple_name, func in self.declares['multipliticy'].items():
            multipliticy = model.evaluate(func).as_long()
            if multipliticy == 0:
                continue
            assignments = {}
            for term_name in self.declares['tuples'].get(tuple_name):
                term = self.declares['terms'].get(term_name)
                # print(f'term: {term.symbol} --> {model.evaluate(term.symbol)} --> multipliticy: {multipliticy}')
                concrete = convert_z3ref_to_concrete(model.evaluate(term.symbol))
                if concrete is not None:
                    assignments[term_name] = concrete
                    
            results[tuple_name] = {
                'assignments' : assignments,
                'multipliticy' : multipliticy
            }
        return results
    
    def from_integrity_constraints(self, instance):
        smt_exprs = []
        primary_key_constraints = []  ## primary key or unique key
        foreign_key_constraints = []  ## foreign key

        for table_name, table in instance._tables.items():
            expressions = []
            for x, y in combinations(list(table.get_tuples()), 2):
                mx = self.multipliticy_func(z3.StringVal(x.this, ctx = self.ctx), z3.StringVal(table_name, ctx = self.ctx))
                my = self.multipliticy_func(z3.StringVal(y.this, ctx = self.ctx), z3.StringVal(table_name, ctx = self.ctx))
                pks = []
                for pk_expr in table.primary_key.expressions:
                    rid = table.get_column_index(pk_expr.name)
                    pks.append(exp.NEQ(this = x.get(rid), expression = y.get(rid)))
                if pks:
                    unique = reduce(lambda x, y : exp.And(this = uexpr.UPredicate(this = x), expression = uexpr.UPredicate(this = y)), pks)
                    smt_expr = self.from_uexpr(unique)
                    expressions.append(mx * my * smt_expr == 1)
            if expressions:
                primary_key_constraints.append(sum(expressions) == len(expressions))

            for fk in table.foreign_keys:
                dep_table = fk.args.get('reference').find(exp.Table)
                to_table_name= dep_table.text('this')
                from_col = fk.expressions[0].text('this')
                to_col = dep_table.expressions[0].name if dep_table.args.get('expressons') else instance.get_table(to_table_name).primary_key.expressions[0].name
                

                # from_col, to_table_name, to_col = get_fk_from_column(fk)
                # S(t′) = S(t′) × ∑ t R(t) × [t.k = t′.k′]
                to_table = instance.get_table(to_table_name)
                for row in table.get_tuples():
                    st = uexpr.Relation(this = row.this, table = table_name)
                    t_pri = row.get(table.get_column_index(from_col))

                    uexprs = []
                    for dep in to_table.get_tuples():
                        rt = uexpr.Relation(this = dep.this, table = to_table_name)
                        tid = dep.get(to_table.get_column_index(to_col))
                        smt_expr = uexpr.UPredicate(this = exp.EQ(this = t_pri, expression = tid))
                        uexprs.append(uexpr.UMul(this = uexpr.UMul(this = st, expression = rt), expression = smt_expr))
                    foreign_key_constraints.append(self.from_uexpr(exp.EQ(this = uexpr.USummation(expressions = uexprs) , expression = exp.Literal.number(1))))
        return primary_key_constraints, foreign_key_constraints

    def from_uexpr(self, expression, **kwargs):

        # print(f'expression: {repr(expression)}')
        if isinstance(expression, uexpr.USummation):
            smt_exprs = []
            for expr in expression.expressions:
                res = self.from_uexpr(expr, **kwargs)
                smt_exprs.append(res)
            return sum(smt_exprs)
        elif isinstance(expression, uexpr.UPredicate):
            return self.from_upredicate(expression, **kwargs)
        
        elif isinstance(expression, uexpr.Relation):
            return self.from_relation(expression, **kwargs)
        
        elif isinstance(expression, exp.Predicate):
            return self.from_predicate(expression, **kwargs)
        
        elif isinstance(expression, uexpr.UConnector):
            return self.from_uconnector(expression, **kwargs)
        
        elif isinstance(expression, exp.Connector):
            return self.from_connector(expression, **kwargs)
        
        elif isinstance(expression, exp.Binary):
            return self.from_binary(expression, **kwargs)
        
        elif isinstance(expression, exp.Literal):
            return self.from_literal(expression, **kwargs)
        elif isinstance(expression, exp.Column):
            return self.from_column(expression, **kwargs)
        elif isinstance(expression, exp.Func):
            return self.from_func(expression, **kwargs)
        elif isinstance(expression, exp.Not):
            # this = self.from_uexpr(expression.this)
            # def push_not(node):
            #     # Base case: if it's not a Not expression, just return the expression
            #     if not node.decl().kind() == z3.Z3_OP_NOT:
            #         return node
                
            #     # Get the inner expression inside the Not
            #     inner = node.arg(0)

            #     # If the inner expression is an If expression, apply De Morgan's law
            #     if inner.decl().kind() == z3.Z3_OP_ITE:
            #         condition = inner.arg(0)
            #         then_expr = inner.arg(1)
            #         else_expr = inner.arg(2)
                    
            #         # Push the Not into the If expression
            #         return z3.If(condition, push_not(z3.Not(then_expr)), push_not(z3.Not(else_expr)))
                
            #     # If it's some other expression inside the Not, return the negation of it
            #     # (assuming it can be simplified at higher levels)
            #     return z3.Not(inner)
            # return push_not(this)

            return z3.Not(self.from_uexpr(expression.this), ctx = self.ctx)
        elif isinstance(expression, exp.Null):
            datatype = expression.args.get('datatype')
            null_val = LABELED_NULL.get(str(datatype))
            return null_val
        elif isinstance(expression, exp.Paren):
            return self.from_uexpr(expression.this, **kwargs)
        elif isinstance(expression, exp.Cast):

            from_type = expression.this.args.get('datatype')
            to_type = expression.args.get('to')
            return self.from_uexpr(expression.this)
            
            ...
        else:
            print(repr(expression))

            raise RuntimeError(f'unsupported uexpr: {expression}')
    
    def from_relation(self, expression, **kwargs):
        tuple_name = expression.this
        table_name = expression.args.get('table')
        declared_func = self.multipliticy_func(z3.StringVal(tuple_name, ctx = self.ctx), z3.StringVal(table_name, ctx = self.ctx))
        self.declares['multipliticy'][tuple_name] = declared_func
        self.declares['relations'].add(table_name)
        return declared_func
    
    def from_upredicate(self, expression, **kwargs):
        this = self.from_uexpr(expression.this, **kwargs)
        # this = this if isinstance(this, z3.BoolRef) else this.symbol

        return z3.If(this, 1, 0, ctx = self.ctx)
    def from_uconnector(self, expression, **kwargs):
        left = self.from_uexpr(expression.this, **kwargs)
        right = self.from_uexpr(expression.expression,  **kwargs)
        operations = {
            'uadd': lambda x, y: x + y,
            'umul': lambda x, y: x * y,
        }
        return operations[expression.key](left, right)
    def from_connector(self, expression, **kwargs):
        left = self.from_uexpr(expression.left, **kwargs)
        right = self.from_uexpr(expression.right, **kwargs)

        operations = {
            'and': lambda x, y : z3.And(x, y), #z3.And(x, y), # x.logical_and(y)
            'or':  lambda x, y: z3.Or(x, y)#x.logical_or(y)
        }

        return operations[expression.key](left, right)
    def from_binary(self, expression, **kwargs):
        left = self.from_uexpr(expression.left, **kwargs)
        right = self.from_uexpr(expression.right, **kwargs)

        # print(repr(left))
        # print('***' *10)

        operations = {
            "add": lambda x, y : x + y,
            "sub": lambda x, y: x - y,
            "mul": lambda x, y: x * y,
            "div": lambda x, y : x / y
        }
        return operations[expression.key](left, right)
    
    def from_predicate(self, expression, **kwargs):
        left = self.from_uexpr(expression.left, **kwargs)
        right = self.from_uexpr(expression.right, **kwargs)
        operations = {
            'like': lambda x, y : like(x, y),
            'gt': lambda x, y : x > y,
            'gte': lambda x, y : x >= y,
            'lt': lambda x, y : x < y,
            'lte': lambda x, y : x <= y,
            'eq': lambda x, y : x == y,
            'neq': lambda x, y : x != y,
        }
        return operations[expression.key](left, right)
    def from_literal(self, expression, **kwargs):
        if expression.is_number:
            return float(expression.this)
        return expression.this
        
    
    def from_column(self, expression):
        term = expression.args.get('term')
        self.declares['terms'][str(term.symbol)] = term
        self.declares['tuples'][expression.args.get('t')].add(str(term.symbol))
        self.declare_context_constraints(term)
        return term.symbol
    
    def from_unary(self, expression, **kwargs):
        this =  self.from_uexpr(expression.this, **kwargs)
        
        
        operations = {
            'is_null' : lambda x : x == LABELED_NULL[str(x.sort()).upper()],
            'not' : lambda x : z3.Not(x, ctx = self.ctx),
            'neg' : lambda x: -x,
        }
        return operations[expression.key](this)
    
    def from_func(self, expression, **kwargs):

        operations = {
            'if' : lambda x: self.evaluate_if(x, **kwargs) ,
            'cast': lambda x: cast(self.from_uexpr(x.this, **kwargs), from_type =  x.this.args.get('datatype'), to_type = x.args.get('to'))
        }

       

        # STRFTIME
        return operations[expression.key](expression)
    
    def evaluate_if(self, expression, **kwargs):
        this = self.from_uexpr(expression.this)
        true = self.from_uexpr(expression.args.get('true'))
        false = self.from_uexpr(expression.args.get('false'))

        return  z3.If(this, true, false, ctx = self.ctx)
    
    



