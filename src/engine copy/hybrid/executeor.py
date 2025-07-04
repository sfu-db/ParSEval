from typing import Any, List, Any, Tuple, Dict, TYPE_CHECKING
from collections import defaultdict
import datetime, logging
from sqlglot import exp
from dataclasses import dataclass, asdict, field
from functools import reduce
from src.context import PathChangeTracker
from src.expr import NULL_VALUES, Symbol, Row, Step, split_conditions
from src.expr import create_symbol
from src.instance import Instance
from ..coverage import Coverage

if TYPE_CHECKING:
    from src.context import Context

logger = logging.getLogger('src.parseval.hybrid')

def make_null(condition, values):
    tup = [v.nullif(condition) for v in values]
    return Row(expressions = tup, multiplicity = values.multiplicity)


@dataclass
class SymbolTable:
    _id: str
    data: List
    expr: List
    op_exprs: List
    label: str = field(default = 'POSITIVE')
    def update(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self

class HybridExecutor:
    def __init__(self, context, add):
        self.context = context
        self.add = add
        self.max_iterations = 1000  # Prevent infinite loops
        self.iteration_count = 0

    def __call__(self, root, instance, *args: Any, **kwds: Any) -> Any:
        self.coverage = Coverage()
        self.iteration_count = 0
        try:
            return self.execute(root=root, instance=instance)
        except Exception as e:
            logging.error(f"Error executing expression: {e}")
            raise
    
    def execute(self, root, **kwargs) -> Any:
        if self.iteration_count >= self.max_iterations:
            raise ValueError("Maximum iteration count exceeded - possible infinite loop")
        self.iteration_count += 1
        
        fname = 'execute_%s' % root.key
        func = getattr(self, fname)
        if func is None:
            raise ValueError(f'Could not process {root.key} - method not found')
        return func(root, **kwargs)
    
    def execute_scan(self, operator: Step, **kwargs):
        instance: Instance = kwargs.get('instance')
        if instance is None:
            raise ValueError("Instance is required for scan operation")
            
        inputs = instance.get_table(operator.table)
        if inputs is None:
            raise ValueError(f"Table {operator.table} not found in instance")
            
        output, smt_exprs, op_exprs = [], [], []
        try:
            for row in inputs:
                output.append(row)
                self.coverage.traceit(operator.key, operator.i(), 1, 'path')
        except Exception as e:
            logging.error(f"Error processing scan operation: {e}")
            raise
            
        return SymbolTable(_id=operator.i(), data=output, expr=smt_exprs, op_exprs=op_exprs)

    def execute_project(self, operator: Step, **kwargs):
        try:
            p = self.execute(operator.this, **kwargs)
            output, smt_exprs = [], []
            for row in p.data:
                projections = [self.execute(project, row=row) for project in operator.projections]
                r = Row(expressions=projections, multiplicity=row.multiplicity)
                output.append(r)
            p.update(_id=operator.i(), data=output)
            return p
        except Exception as e:
            logging.error(f"Error processing project operation: {e}")
            raise

    def execute_filter(self, operator: Step, **kwargs):
        try:
            p = self.execute(operator.this, **kwargs)
            outputs, smt_exprs, op_exprs = [], [], []
            for row in p.data:
                with PathChangeTracker(self.context) as tracker:
                    branch = False
                    sat = self.execute(operator.condition, row=row, **kwargs)
                    if sat:
                        branch = True
                        outputs.append(row)
                    smt = tracker.get_delta()
                    self.add.which_branch(operator.key, operator.i(), smt, split_conditions(operator.condition), branch)
            self.add.advance(operator.key, operator.i())
            return p.update(_id=operator.i(), data=outputs, expr=smt_exprs, op_exprs=op_exprs)
        except Exception as e:
            logging.error(f"Error processing filter operation: {e}")
            raise

    def execute_inner_join(self, operator: Step, **kwargs):
        try:
            left_inputs = self.execute(operator.left, **kwargs)
            right_inputs = self.execute(operator.right, **kwargs)
            output, smt_exprs, op_exprs = [], [], []
            kws = {k: v for k, v in kwargs.items() if k != 'row'}
            
            for lrow in left_inputs.data:
                with PathChangeTracker(self.context) as tracker:
                    branch = False
                    for rrow in right_inputs.data:
                        row = lrow * rrow
                        sat = self.execute(operator.condition, row=row, **kws)
                        if sat:
                            branch = True
                            output.append(row)
                    smt = tracker.get_delta()
                    self.add.which_branch(operator.key, operator.i(), [logical_any(*smt)], [operator.condition], branch)
                    
            self.add.advance(operator.key, operator.i())
            return SymbolTable(_id=operator.i(), data=output, expr=smt_exprs, op_exprs=op_exprs)
        except Exception as e:
            logging.error(f"Error processing inner join operation: {e}")
            raise

    def execute_join(self, operator: Step, **kwargs):
        join_type = operator.kind
        ### Process Inner Join
        if join_type == 'inner':
            return self.execute_inner_join(operator, **kwargs)
        raise RuntimeError(f'unsupport JOIN OPERATION')
        ### Process Left Join
        # if join_type == 'left':
        #     for lrow, lexpr in zip(left_inputs.data, left_inputs.expr):
        #         tmp, exprs, join_exprs = [], [], []
        #         for rrow, rexpr in zip(right_inputs.data, right_inputs.expr):
        #             row = lrow * rrow
        #             sat = self.execute(operator.condition, row = row, **kws)
        #             if sat:
        #                 tmp.append(row)
        #                 exprs.append(lexpr * Pred(this = sat) * rexpr)
        #                 join_exprs.append(sat * rexpr.to_smt())
        #         if not tmp:  ## If no join
        #             ref = right_inputs.data[0]
        #             null_cond = sum(join_exprs) == 0
        #             r = make_null(null_cond, ref)
        #             output.append(lrow * r)
        #             smt_exprs.append(lexpr * Pred(this = null_cond))
        #         else:
        #             output.extend(tmp)
        #             smt_exprs.extend(exprs)
        # ### Process Right Join


        

        # if not output:
        #     self.path(sum(op_exprs).to_smt() >= int(len(left_inputs.data) / 2), operator.i(), 'new')

        # p = STable(_id = operator.i(), data = output, expr = smt_exprs, op_exprs= op_exprs)
        # return p


    def execute_aggregate_to_single_group(self, operator: Step, **kwargs):
        assert not operator.groupby, f'Func execute_aggregate_to_single_group could only handle aggregate without group keys'
        p = self.execute(operator.this, **kwargs)
        if not p.data:
            return p
        
        
        # create_symbol('int', self.context, expr = operator.key + operator.i() + 'multiplicity', value = 1)

        output, smt_exprs, op_exprs = [], [], []
        group, group_expr = self.initialize_group(self.context, operator.agg_funcs, '0')
        ## there should be a NULL , at least one duplication in group
        for fidx, func in enumerate(operator.agg_funcs):
            group_size = 0
            distinct = func.args.get('distinct')
            func_key = func.key if not distinct else f"{func.key}_distinct"
            func_dtype = str(func.args.get('datatype'))
            ref = func.this.args.get('ref') if func.this.key != 'star' else 0
            null_expr, distinct_exprs = [], []

            for rid, row, expr in zip(range(len(p.data)), p.data, p.expr):
                identifier = f'g{operator.i()}_c{ref}_r{rid}'
                linput = self.execute(func.this, row = row)
                c0 = expr.to_smt() > 0
                c1 = linput.is_null().__not__()
                null_expr.append(Pred(this = c1))
                distinct_exprs.append(row.multiplicity > 1)
                group_size = create_ite(self.context, logical_all(c0, c1), row.multiplicity + group_size,  group_size)

                if func_key in ['avg', 'count']:
                    cnt =  row.multiplicity.value + group[ref]['count'].value - 1 if c0 and c1 else 1
                    cnt_sym = create_symbol('int', self.context, expr = identifier + 'count', value = cnt)
                    smt_expr = cnt_sym == create_ite(self.context, logical_all(c0, c1), group[ref]['count'] + row.multiplicity,  group[ref]['count'])
                    group_expr[ref]['count'].append(smt_expr)
                    group[ref]['count'] = cnt_sym                    
                if func_key in ['avg', 'sum']:
                    v_ = group[ref]['sum'].value + linput.value * row.multiplicity.value - 1 if c0 and c1 else group[ref]['sum'].value
                    sum_sym = create_symbol(func_dtype, self.context, expr = identifier + 'sum', value = v_)
                    smt_expr = sum_sym == create_ite(self.context, logical_all(c0, c1), group[ref]['sum'] + linput,  group[ref]['sum'])
                    group_expr[ref]['sum'].append(smt_expr)
                    group[ref]['sum'] = sum_sym
                if func_key == 'max':
                    ### max
                    v_ = linput.value if c0 and c1 and linput >= group[ref]['max'] else group[ref]['max'].value
                    max_sym = create_symbol(func_dtype, self.context, expr = identifier + 'max', value = v_)
                    smt_expr = max_sym == create_ite(self.context, logical_all(c0, c1, linput >= group[ref]['max']), linput, group[ref]['max'])
                    group_expr[ref]['max'].append(smt_expr)
                    group[ref]['max'] = max_sym
                
                if func_key == 'min':
                    ### min
                    v_ = linput.value if c0 and c1 and linput <= group[ref]['min'] else group[ref]['min'].value
                    min_sym = create_symbol(func_dtype, self.context, expr = identifier + 'min', value = v_)
                    smt_expr = min_sym == create_ite(self.context, logical_all(c0, c1, linput <= group[ref]['min']), linput, group[ref]['min'])
                    group_expr[ref]['min'].append(smt_expr)
                    group[ref]['min'] = min_sym
            
                distinct_constraints = []
                for jid in range(rid + 1, len(p.data)):
                    rinput = self.execute(func.this, row = p.data[jid])
                    distinct_constraints.append(linput != rinput)
                
                distinct_exprs.extend(distinct_constraints)

                if func_key == 'count_distinct':
                    v_ = row.multiplicity.value + group[ref][func_key].value - 1  if all([c0, c1 , *distinct_constraints]) else 1
                    cnt_distinct_sym = create_symbol(func_dtype, self.context, expr = identifier + 'count_distinct', value = v_)
                    smt_expr = cnt_distinct_sym == create_ite(self.context, logical_all(c0, c1 , *distinct_constraints), group[ref]['count_distinct'] + row.multiplicity,  group[ref]['count_distinct'])

                    group[ref]['count_distinct'] = cnt_distinct_sym
                    logger.info(smt_expr)
                
                if func_key == 'sum_distinct':
                    v_ = group[ref][func_key].value + linput.value * row.multiplicity.value - 1 if all([c0, c1 , *distinct_constraints]) else group[ref]['sum'].value
                    sum_distinct_sym = create_symbol(func_dtype, self.context, expr = identifier + 'sum_distinct', value = v_)
                    smt_expr = cnt_distinct_sym == create_ite(self.context,  logical_all(c0, c1 , *distinct_constraints), group[ref]['sum_distinct'] + linput * row.multiplicity,  group[ref]['sum_distinct'])
                    group[ref]['sum_distinct'] = sum_distinct_sym

            if func_key == 'avg':
                output.append(group[ref].get('sum') / group[ref].get('count'))
                self.path(group[ref].get('count') > 0, f'g{operator.i()}_group{ref}_size', 'positive')
                self.path(group[ref].get('sum') > 0, f'g{operator.i()}_group{ref}_sum', 'positive')                
                self.path(sum(null_expr).to_smt() < len(null_expr), f'g{operator.i()}_group{ref}_null', 'negative')
                
            elif func_key == 'count':
                output.append(group[ref].get(func_key))
                self.path(group[ref].get('count') > 0, f'g{operator.i()}_group{ref}_size', 'positive')
                self.path(sum(null_expr).to_smt() < len(null_expr), f'g{operator.i()}_group{ref}_null', 'negative')
                # self.path(logical_any(distinct_exprs), f'g{operator.i()}_group{ref}_distinct', 'negative')
            elif func_key == 'sum':
                output.append(group[ref].get(func_key))
                self.path(group[ref].get('sum') > 0, f'g{operator.i()}_group{ref}_sum', 'positive')
                # self.path(logical_any(distinct_exprs), f'g{operator.i()}_group{ref}_distinct', 'negative')
            else:
                output.append(group[ref].get(func_key))
                # self.path(logical_any(distinct_exprs), f'g{operator.i()}_group{ref}_distinct', 'negative')

            for c in distinct_exprs:
                self.path(c, f'g{operator.i()}_group{ref}_distinct', 'negative')

            self.coverage.traceit(operator.key, operator.i(), f'{func}_NULL', f'group', any([sum(null_expr).to_smt() < len(null_expr)]))
            self.coverage.traceit(operator.key, operator.i(), f'{func}_distinct', f'group', any(distinct_exprs))
        p.update(_id = operator.i(), data = [Row(expressions = output, multiplicity = group_size)])
        return p


    def initialize_group(self, context, aggfuncs, prefix):
        group = defaultdict(dict)
        group_exprs = defaultdict(lambda: defaultdict(list))

        for func in aggfuncs:
            distinct = func.args.get('distinct')
            func_key = func.key if not distinct else f"{func.key}_distinct"
            func_dtype = str(func.args.get('datatype'))
            ref = func.this.args.get('ref') if func.this.key != 'star' else 0
            if func_key == 'avg':
                if 'count' not in group[ref]:
                    count_variable = create_symbol(func_dtype, context, expr = f'aggf{ref}_{prefix}_count', value = 1)
                    group[ref]['count'] = count_variable
                if 'sum' not in group[ref]:
                    sum_variable = create_symbol(func_dtype, context, expr = f'aggf{ref}_{prefix}_sum', value = 1)
                    group[ref]['sum'] = sum_variable
            else:
                variable = create_symbol(func_dtype, context, expr = f'aggf{ref}_{prefix}_{func_key}', value = None)
                group[ref][func_key] = variable
            if func_key in ['count', 'sum', 'count_distinct', 'sum_distinct', 'avg']:
                keys = ['count', 'sum'] if func_key == 'avg' else [func_key]
                for key in keys:
                    if key not in group_exprs[ref]:
                        group[ref][key].value = 1
                        group_exprs[ref][key].append(group[ref][key] == 0)
        return group, group_exprs
    

    def execute_aggregate(self, operator: Step, **kwargs):
        if not operator.groupby:
            return self.execute_aggregate_to_single_group(operator, **kwargs)
        
        def get_groupkey(row):
            return tuple(row[column.args.get('ref')] for column in operator.groupby)
        p = self.execute(operator.this, **kwargs)

        if not p.data:
            return p
        output, smt_exprs, op_exprs = [], [], []
        group_data = defaultdict(list)

        for rid, row, expr in zip(range(len(p.data)), p.data, p.expr):
            lgroup_key = get_groupkey(row)
            group_data[lgroup_key].append((row, expr))

        ### Process Aggregate Operation
        for gidx, (group_key, data) in enumerate(group_data.items()):
            ### ensure same group
            tup, exprs = [*group_key], []
            for row, _ in data:
                rgroup_key = get_groupkey(row)
                c1 = reduce(lambda x, y : x and y, tuple(lkey == rkey for lkey, rkey in zip(lgroup_key, rgroup_key)))
                exprs.append(c1)
                
            for fidx, func in enumerate(operator.agg_funcs):
                d, e_ = getattr(self, f'execute_{func.key}')(func, data = data, step_id = operator.i())
                exprs.extend(e_)
                tup.append(d)
            output.append(Row(expressions = tup, multiplicity = 1))
            smt_exprs.append(Pred(this = logical_all(exprs)))
            self.coverage.traceit(operator.key, operator.i(), f'size', f'group', len(data) > 2)
        
        self.coverage.traceit(operator.key, operator.i(), f'count', f'group', len(output) > 1) 

        ## Ensure Aggregate Constraints
        for func in operator.agg_funcs:
            null_exprs = []
            ref = func.this.args.get('ref') if func.this.key != 'star' else 0
            for gidx, (group_key, data) in enumerate(group_data.items()):
                ### ensure same group
                tup, exprs = [*group_key], []
                for row, expr in data:
                    func_input = self.execute(func.this, row = row)
                    null_exprs.append(logical_all(func_input.is_null(), expr.to_smt() > 0))

            self.coverage.traceit(operator.key, operator.i(), f'{func.key}_{ref}_NULL', f'group', any(null_exprs))            
            self.path(logical_any(null_exprs), f'{func.key}_{ref}_NULL', 'negative')

        return p.update(_id = operator.i(), data = output, expr = smt_exprs)
              


    def execute_sort(self, operator: Step, **kwargs):
        p = self.execute(operator.this, **kwargs)
        limit = operator.args.get('limit') or 1
        offset = operator.args.get('offset') or 0
        desc = operator.args.get('dir') # ['ASCENDING', 'DESCENDING]
        sorts = [col['column'] for col in operator.args.get('sort')]

        cnt = limit + offset 
        multiplicities = []
        output, smt_exprs = [], []
        for row, expr in zip(p.data, p.expr):
            multiplicities.append(create_ite(self.context, expr.to_smt() > 0, row.multiplicity, 0))
            if all([not row[idx].is_null() for idx in sorts]):
                smt_exprs.append(expr)
                output.append(row)
        e_ = sum(multiplicities) >= cnt
        self.path(e_, operator.i() + 'count', 'positive')

        p.update(_id = operator.i(), data = output, expr = smt_exprs)
        return p

        
    def execute_count(self, operator, **kwargs):
        data = kwargs.get('data')
        step_id = kwargs.get('step_id')
        distinct = operator.args.get('distinct')
        func_key = operator.key if not distinct else f"{operator.key}_distinct"
        func_dtype = str(operator.args.get('datatype'))
        ref = operator.this.args.get('ref') if operator.this.key != 'star' else 0
        prefix = f'o{step_id}_{func_key}_r{ref}'
        count = create_symbol(func_dtype, self.context, expr = f'{prefix}_0', value = 0)
        e_ = [count == 0]
        
        if not distinct:
            for rid, (row, expr) in enumerate(data):
                func_input = self.execute(operator.this, row = row)
                c1 = expr.to_smt() > 0
                c2 = func_input.is_null().__not__()
        
                if c1 and c2:
                    v_ = row.multiplicity.value + count.value
                    sym = create_symbol(func_dtype, self.context, expr = f'{prefix}_{rid+1}', value = v_)
                    c3 = sym == count + row.multiplicity
                    count = sym
                    e_.extend((c1, c2, c3))
        else:
            ...
        
        

        return count, e_

    def execute_sum(self, operator, **kwargs):
        data = kwargs.get('data')
        step_id = kwargs.get('step_id')
        distinct = operator.args.get('distinct')
        func_key = operator.key if not distinct else f"{operator.key}_distinct"
        func_dtype = str(operator.args.get('datatype'))
        ref = operator.this.args.get('ref') if operator.this.key != 'star' else 0
        prefix = f'o{step_id}_{func_key}_r{ref}'
        total = create_symbol(func_dtype, self.context, expr = f'{prefix}_0', value = 0)
        e_ = [total == 0]
        if not distinct:
            for rid, (row, expr) in enumerate(data):
                func_input = self.execute(operator.this, row = row)
                c1 = expr.to_smt() > 0
                c2 = func_input.is_null().__not__()
                if c1 and c2:
                    v_ = row.multiplicity.value * func_input.value + total.value
                    sym = create_symbol(func_dtype, self.context, expr = f'{prefix}_{rid+1}', value = v_)
                    c3 = sym == total + func_input * row.multiplicity
                    total = sym
                    e_.extend((c1, c2, c3))
        else:
            ...
        
        return total, e_

    def execute_max(self, operator, **kwargs):
        data = kwargs.get('data')
        step_id = kwargs.get('step_id')
        distinct = operator.args.get('distinct')
        func_key = operator.key if not distinct else f"{operator.key}_distinct"
        func_dtype = str(operator.args.get('datatype'))
        ref = operator.this.args.get('ref') if operator.this.key != 'star' else 0
        prefix = f'o{step_id}_{func_key}_r{ref}'
        
        max_ = None
        e_ = []
        for rid, (row, expr) in enumerate(data):
            func_input = self.execute(operator.this, row = row)
            c1 = expr.to_smt() > 0
            c2 = func_input.is_null().__not__()
            
            if max_ is None:
                max_ = func_input
                e_.append((c1, c2))
            else:
                c3 = func_input >= max_
                if c3:
                    max_ = func_input
                    e_.append((c1, c2, c3))
                else:
                    e_.append((c1, c2, c3.__not__()))
        return max_, e_

    def execute_min(self, operator, **kwargs):
        data = kwargs.get('data')
        step_id = kwargs.get('step_id')
        distinct = operator.args.get('distinct')
        func_key = operator.key if not distinct else f"{operator.key}_distinct"
        func_dtype = str(operator.args.get('datatype'))
        ref = operator.this.args.get('ref') if operator.this.key != 'star' else 0
        prefix = f'o{step_id}_{func_key}_r{ref}'
        
        min_ = None
        e_ = []
        for rid, (row, expr) in enumerate(data):
            func_input = self.execute(operator.this, row = row)
            c1 = expr.to_smt() > 0
            c2 = func_input.is_null().__not__()
            if min_ is None:
                min_ = func_input
                e_.append((c1, c2))
            else:
                c3 = func_input <= min_
                if c3:
                    min_ = func_input
                    e_.append((c1, c2, c3))
                else:
                    e_.append((c1, c2, c3.__not__()))

        return min_, e_

    

        
    def execute_star(self, operator, **kwargs):
        row = kwargs.get('row')
        expr = row[0]
        if hasattr(expr, 'dtype'): self.context[4].add(expr.expr) 
        return expr

    def execute_neg(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.__neg__()
   
    def execute_not(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.__not__()

    def execute_or(self, operator: exp, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        return left.or_(right)
    
    def execute_and(self, operator, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        return left.and_(right)
    
    def execute_column(self, operator,**kwargs):
        row = kwargs.get('row')
        term = row[int(operator.args.get('ref'))]
        self.context.set('used_symbols', term.expr)
        # if term.dtype == 'String':
        #     self.add.add(term.length > 0 , 'integrity', 'positive')
        return term

    def execute_literal(self, operator, **kwargs):
        dtype = operator.args.get('datatype')
        if dtype:
            typ = exp.DataType.build(dtype)
            if typ.is_type(*exp.DataType.TEXT_TYPES):
                return str(operator.this)
            elif typ.is_type(*exp.DataType.INTEGER_TYPES):
                return int(operator.this)
            elif typ.is_type(*exp.DataType.REAL_TYPES):
                return float(operator.this)
        return operator.this

ops =[
      ("gt", ">" ),\
      ("gte", ">="),\
      ("lt", "<"),\
      ("lte", "<="),\
      ("eq", "=="),\
      ("neq", "!=")]

def make_method(method, op):
    code = "def %s(self, operator, **kwargs):\n" % method
    code += "   left = self.execute(operator.this, **kwargs)\n"
    code += "   right = self.execute(operator.expression, **kwargs)\n"
    code += "   result = left %s right \n" % op
    code += "   parent = operator.find_ancestor(Step)\n"
    # code += "   if parent.key != 'join':\n"
    # code += "      self.coverage.traceit(parent.key, parent.i(), str(operator), 'Predicate', result.value)\n"
    code += "   return result"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(HybridExecutor, method, locals_dict[method])

for (name, op) in ops:
    method = "execute_%s" % name
    make_method(method, op)

binary_ops = [    
    ('mul', '*'),
    ('add', '+'),
    ('sub', '-')
]

def make_binary_method(method, op):
    code = "def %s(self, operator, **kwargs):\n" % method
    code += "   left = self.execute(operator.this, **kwargs)\n"
    code += "   right = self.execute(operator.expression, **kwargs)\n"
    code += "   result = left %s right \n" % op
    code += "   return result"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(HybridExecutor, method, locals_dict[method])

for (name, op) in binary_ops:
    method = "execute_%s" % name
    make_binary_method(method, op)

