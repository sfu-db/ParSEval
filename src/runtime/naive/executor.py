


from typing import Any, List, Any, Tuple, Dict
from src.uexpr.rex import *
from collections import defaultdict
import datetime, logging
from dataclasses import dataclass, asdict, field
from functools import reduce

logger = logging.getLogger('src.naive')

LABELED_NULL = {
    'INT' : 6789,
    'REAL' : 0.6789,
    'STRING' : 'NULL',
    'BOOLEAN' : 'NULL',
    'DATETIME' : datetime.datetime(1970, 1, 1, 0, 0, 0),
    'DATE' : datetime.date(1970, 1, 1),
}

@dataclass
class Branch:
    _id: str
    data: List
    expr: List
    constraints: defaultdict = field(default_factory=lambda: defaultdict(list))
    label: str = field(default = 'POSITIVE')

    def update(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self

def merge_dict(a, b):
    return defaultdict(list, {key: a[key] + b[key] for key in set(a) | set(b)})
def make_null(condition, values):
    tup = [v.nullif(condition) for v in values]
    return Row(expressions = tup, multiplicity = values.multiplicity)

def ensure_group_initialization(context, group: Dict[str, Dict[str, Any]], ref, func_key, func_dtype, prefix = None):
    if ref in group and func_key in group[ref]:
        return
    if func_key == 'avg': #  and func_key != 'avg'
        if 'count' not in group[ref]:
            count_variable = ssa_factory.create_symbol(func_dtype, context, expr = f'aggf{ref}_{prefix}_count', value = None)
            group[ref]['count'] = count_variable
        if 'sum' not in group[ref]:
            sum_variable = ssa_factory.create_symbol(func_dtype, context, expr = f'aggf{ref}_{prefix}_sum', value = None)
            group[ref]['sum'] = sum_variable
    else:
        variable = ssa_factory.create_symbol(func_dtype, context, expr = f'aggf{ref}_{prefix}_{func_key}', value = None)
        group[ref][func_key] = variable

class NaiveExecutor:
    def __init__(self, context, path):
        self.context = context
        self.path = path
        self.coverage = Coverage()
        self.result = []
        self.negatives = defaultdict(list)

    def __call__(self, root, instance, *args: Any, **kwds: Any) -> Any:
        strategy = kwds.pop('strategy', 'positive')
        return self.execute(root = root,  instance = instance, strategy = strategy)
    
    def execute(self, root, **kwargs) -> Any:
        fname = 'execute_%s' % root.key
        func = getattr(self, fname)
        assert func is not None, f'could not process {root.key}'
        return func(root, **kwargs)

    def execute_scan(self, operator, **kwargs) -> Branch:
        instance: Instance = kwargs.get('instance')
        inputs = instance.get_table(operator.table)
        output, exprs  = [], []
        for row in inputs:
            output.append(row)
            self.coverage.traceit(operator.key, 1, 'path')
            exprs.append(Term(this = row.multiplicity))
            self.path(row.multiplicity >= 0 , 'integrity', 'positive')
        return  Branch(_id = operator.i(), data = output, expr= exprs)
    
    def execute_project(self, operator, **kwargs):
        p = self.execute(operator.this, **kwargs)
        output = []
        for row in p.data:
            projections = [self.execute(project, row = row) for project in operator.projections]
            r = Row(expressions = projections, multiplicity = row.multiplicity)
            output.append(r)
        p.update(_id = operator.i(), data = output)
        return p

    def execute_filter(self, operator: Step, **kwargs):
        p = self.execute(operator.this, **kwargs)
        output, smt_exprs, op_exprs = [], [], []
        _kwargs = {k: v for k,v in kwargs.items() if k !='row'}
        for row, expr in zip(p.data, p.expr):
            sat = self.execute(operator.condition, row = row, **_kwargs)
            smt_exprs.append(expr * Pred(this = sat))
            output.append(row)
            op_exprs.append(sat)

        if kwargs.pop('strategy', 'positive') != 'positive':
            row_cnt_constraints = sum(set(op_exprs)) > max( int(len(set(op_exprs)) / 2), 2)
            self.path(row_cnt_constraints, 'row_cnt', 'negative')

        return p.update(_id = operator.i(), data = output, expr = smt_exprs)
    
    def execute_join(self, operator: Step, **kwargs):
        left_inputs = self.execute(operator.left, **kwargs)
        right_inputs = self.execute(operator.right, **kwargs)
        output, smt_exprs, op_exprs = [], [], []
        join_type = operator.kind
        _kwargs = {k:v for k, v in kwargs.items() if k != 'row'}

        ### Process Inner Join
        if join_type == 'inner':
            for lrow, lexpr in zip(left_inputs.data, left_inputs.expr):
                exprs = []
                for rrow, rexpr in zip(right_inputs.data, right_inputs.expr):
                    row = lrow * rrow
                    sat = self.execute(operator.condition, row = row, **_kwargs)
                    output.append(row)
                    smt_exprs.append(lexpr * Pred(this = sat) * rexpr)
                    exprs.append(sat)
                op_exprs.append(ssa_factory.sall(*(s.__not__() for s in exprs)))

            # if kwargs.pop('strategy', 'positive') != 'positive':
            #     row_cnt_constraints = sum(set(op_exprs)) > max( int(len(set(op_exprs)) / 2), 2)
            #     self.path(row_cnt_constraints, 'row_cnt', 'negative')

            self.path(ssa_factory.sany(*op_exprs), 'NEG_o' + operator.i() +'_' + str(operator.condition), 'negative')

        ### Process Left Join
        if join_type == 'left':
            for lrow, lexpr in zip(left_inputs.data, left_inputs.expr):
                exprs, neg_exprs = [], []
                for rrow, rexpr in zip(right_inputs.data, right_inputs.expr):
                    row = lrow * rrow
                    sat = self.execute(operator.condition, row = row, **_kwargs)
                    exprs.append(sat * rexpr.to_smt())
                    neg_exprs.append(sat)
                op_exprs.append(ssa_factory.sall(*(s.__not__() for s in neg_exprs)))
                for rrow in right_inputs.data:
                    row = lrow * rrow
                    sat = self.execute(operator.condition, row = row, **_kwargs)
                    null_cond = sum(exprs) * sat == 0
                    r = make_null(null_cond, rrow)
                    output.append(lrow * r)
                    smt_exprs.append(lexpr * Pred(this = null_cond))

            self.path(ssa_factory.sany(*op_exprs), 'NEG_o' + operator.i() +'_' + str(operator.condition), 'negative')

        ### Process Right Join
        if join_type == 'right':
            for rrow, rexpr in zip(right_inputs.data, right_inputs.expr):
                exprs, neg_exprs = [], []
                for lrow, lexpr in zip(left_inputs.data, left_inputs.expr):
                    row = rrow * lrow
                    sat = self.execute(operator.condition, row = row, **_kwargs)
                    exprs.append(lexpr.to_smt() * sat)
                for lrow in left_inputs.data:
                    row = rrow * lrow
                    sat = self.execute(operator.condition, row = row, **_kwargs)
                    null_cond = sum(exprs) * sat == 0
                    r = make_null(null_cond, rrow)
                    output.append(rrow * r)
                    smt_exprs.append(Pred(this = null_cond) * rexpr)
        p = Branch(_id = operator.i(), data = output, expr = smt_exprs, constraints= merge_dict(left_inputs.constraints, right_inputs.constraints))
        return p

    


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
            multiplicities.append(ssa_factory.ite(self.context, expr.to_smt() > 0, row.multiplicity, 0))
            if all([not row[idx].is_null() for idx in sorts]):
                smt_exprs.append(expr)
                output.append(row)
        
        e_ = sum(multiplicities) >= cnt
        self.path(e_, operator.i() + 'count', 'positive')

        p.update(_id = operator.i(), data = output, expr = smt_exprs)
        return p

    

    def execute_aggregate_to_single_group(self, operator: Step, **kwargs):
        '''
            Handle cases like COUNT(DISTINCT ), COUNT(), SUM() ... without GROUP BY COLUMNS
        '''
        assert not operator.groupby, f'Func execute_aggregate_to_single_group could only handle aggregate without group keys'
        p = self.execute(operator.this, **kwargs)
        group = defaultdict(lambda: defaultdict(dict))
        cardinality = defaultdict(lambda: defaultdict(list))

        output = []
        exprs = []
        for func in operator.agg_funcs:
            distinct = func.args.get('distinct')
            func_key = func.key if not distinct else f"{func.key}_distinct"
            func_dtype = str(func.args.get('datatype'))
            ref = func.this.args.get('ref') if func.this.key != 'star' else 0
            ensure_group_initialization(self.context, group, ref, func_key, func_dtype, prefix = 0)
            for k in ['count', 'sum', 'count_distinct', 'sum_distinct']:
                if k in group[ref]:
                    cardinality[ref][k].append(group[ref][k] == 0)
            
            for rid, row, expr in zip(range(len(p.data)), p.data, p.expr):
                identifier = f'g{operator.i()}_c{ref}_r{rid}'
                linput = self.execute(func.this, row = row)
                c0 = expr.to_smt() > 0
                c1 = linput.is_null().__not__()
                if func_key in ['avg', 'count']:
                    cnt_sym = ssa_factory.create_symbol('int', self.context, expr = identifier + 'count', value = 1)
                    smt_expr = cnt_sym == ssa_factory.ite(self.context, c0 and c1, group[ref]['count'] + row.multiplicity,  group[ref]['count'])
                    cardinality[ref]['count'].append(smt_expr)
                    group[ref]['count'] = cnt_sym
                
                if func_key in ['avg', 'sum']:
                    sum_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'sum', value = group[ref]['sum'].value)
                    smt_expr = sum_sym == ssa_factory.ite(self.context, c0 and c1, group[ref]['sum'] + linput,  group[ref]['sum'])
                    cardinality[ref]['sum'].append(smt_expr)
                    group[ref]['sum'] = sum_sym
                
                if func_key == 'max':
                    ### max
                    max_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'max', value = None)
                    smt_expr = max_sym == ssa_factory.ite(self.context, c0 and c1 and linput >= group[ref]['max'], linput, group[ref]['max'])
                    cardinality[ref]['max'].append(smt_expr)
                    group[ref]['max'] = max_sym
                if func_key == 'min':
                    ### min
                    min_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'min', value = None)
                    smt_expr = min_sym == ssa_factory.ite(self.context, c0 and c1 and linput <= group[ref]['min'], linput, group[ref]['min'])
                    cardinality[ref]['min'].append(smt_expr)
                    group[ref]['min'] = min_sym
                if func_key in ['count_distinct', 'sum_distinct']:
                    ### distinct count
                    distinct_constraints = []
                    for jid in range(rid + 1, len(p.data)):
                        rinput = self.execute(func.this, row = p.data[jid])
                        distinct_constraints.append(linput != rinput)
                    d1 = sum(distinct_constraints)
                    if func_key == 'count_distinct':
                        cnt_distinct_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'count_distinct', value = None)
                        smt_expr = cnt_distinct_sym == ssa_factory.ite(self.context, c0 and c1 and d1 == len(distinct_constraints), group[ref]['count_distinct'] + row.multiplicity,  group[ref]['count_distinct'])
                        group[ref]['count_distinct'] = cnt_distinct_sym
                    
                    if func_key == 'sum_distinct':
                        sum_distinct_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'sum_distinct', value = None)
                        smt_expr = cnt_distinct_sym == ssa_factory.ite(self.context, c0 and c1 and d1 == len(distinct_constraints), group[ref]['sum_distinct'] + linput * row.multiplicity,  group[ref]['sum_distinct'])
                        group[ref]['sum_distinct'] = sum_distinct_sym
                    
            if func_key == 'avg':
                output.append(group[ref].get('sum') / group[ref].get('count'))
            else:
                output.append(group[ref].get(func_key))

            if func_key in ['avg', 'count']:
                self.path(group[ref].get('count') > 0, f'g{operator.i()}_group_size', 'positive')
            
            if func_key in ['avg', 'sum']:
                self.path(group[ref].get('sum') > 0, f'g{operator.i()}_group_sum', 'positive')


        p.update(_id = operator.i(), data = [Row(expressions = output, multiplicity = 1)])
        return p



    # def execute_aggregate_single_group(self, operator: Step , **kwargs):
    #     '''
    #         Handle cases like COUNT(DISTINCT ), COUNT(), SUM() ... without GROUP BY COLUMNS
    #     '''
    #     assert not operator.groupby, f'Func execute_aggregate_single_group could only handle aggregate without group keys'
    #     p = self.execute(operator.this, **kwargs)

    #     groups = {}
    #     cardinality = defaultdict(lambda: defaultdict(list))
    #     output = []
    #     for fidx, func in enumerate(operator.agg_funcs):
    #         if func.this.key == 'star':
    #             continue
            
    #         func_dtype = str(func.args.get('datatype'))
    #         ref_dtype = func.this.args.get('datatype')
    #         # func_key = func.key if not distinct else f"{func.key}_distinct"
    #         ref = func.this.args.get('ref')
    #         if ref in groups :
    #             continue
     

    #         groups[ref] = {
    #             'count': ssa_factory.create_symbol('int', self.context, expr = f'ref_{ref}_count', value = 1),
    #             'sum': ssa_factory.create_symbol(func_dtype, self.context, expr = f'ref_{ref}_sum', value = 1),
    #             'max': ssa_factory.create_symbol(func_dtype, self.context, expr = f'ref_{ref}_max', value = None),
    #             'min': ssa_factory.create_symbol(func_dtype, self.context, expr = f'ref_{ref}_min', value = None),
    #             'count_distinct': ssa_factory.create_symbol('int', self.context, expr = f'ref_{ref}_cnt_distinct', value = 0),
    #             'sum_distinct': ssa_factory.create_symbol(func_dtype, self.context, expr = f'ref_{ref}_sum_distinct', value = 0)
    #         }

    #         cardinality[ref]['count'].append(groups[ref]['count'] == 0)

    #         if ref_dtype and ref_dtype.is_type(*exp.DataType.NUMERIC_TYPES):
    #             cardinality[ref]['sum'].append(groups[ref]['sum'] == 0)
            
    #         for rid, row, expr in zip(range(len(p.data)), p.data, p.expr):
    #             identifier = f'g{operator.i()}_c{ref}_r{rid}'
    #             linput = self.execute(func.this, row = row)
    #             c0 = expr.to_smt() > 0
    #             c1 = linput.is_null().__not__()

    #             ### count
    #             cnt_sym = ssa_factory.create_symbol('int', self.context, expr = identifier + 'count', value = 1)
    #             smt_expr = cnt_sym == ssa_factory.ite(self.context, c0 and c1, groups[ref]['count'] + row.multiplicity,  groups[ref]['count'])
    #             cardinality[ref]['count'].append(smt_expr)
                
    #             ### distinct count
    #             distinct_constraints = []
    #             for jid in range(rid + 1, len(p.data)):
    #                 rinput = self.execute(func.this, row = p.data[jid])
    #                 distinct_constraints.append(linput != rinput)
    #             d1 = sum(distinct_constraints)
    #             cnt_distinct_sym = ssa_factory.create_symbol('int', self.context, expr = identifier + 'count_distinct', value = None)
    #             smt_expr = cnt_distinct_sym == ssa_factory.ite(self.context, c0 and c1 and d1 == len(distinct_constraints), groups[ref]['count_distinct'] + row.multiplicity,  groups[ref]['count_distinct'])

    #             groups[ref]['count'] = cnt_sym
    #             groups[ref]['count_distinct'] = cnt_distinct_sym
                
    #             ### sum
    #             if ref_dtype and not ref_dtype.is_type(*exp.DataType.NUMERIC_TYPES):
    #                 continue

    #             sum_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'sum', value = groups[ref]['sum'].value)

    #             smt_expr = sum_sym == ssa_factory.ite(self.context, c0 and c1, groups[ref]['sum'] + linput,  groups[ref]['sum'])
    #             cardinality[ref]['sum'].append(smt_expr)
    #             ### distinct sum
    #             sum_distinct_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'sum_distinct', value = None)
    #             smt_expr = cnt_distinct_sym == ssa_factory.ite(self.context, c0 and c1 and d1 == len(distinct_constraints), groups[ref]['sum_distinct'] + linput * row.multiplicity,  groups[ref]['sum_distinct'])

    #             ### max
    #             max_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'max', value = None)
    #             smt_expr = max_sym == ssa_factory.ite(self.context, c0 and c1 and linput >= groups[ref]['max'], linput, groups[ref]['max'])
    #             cardinality[ref]['max'].append(smt_expr)

    #             ### min
    #             min_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = identifier + 'min', value = None)
    #             smt_expr = min_sym == ssa_factory.ite(self.context, c0 and c1 and linput <= groups[ref]['min'], linput, groups[ref]['min'])
    #             cardinality[ref]['min'].append(smt_expr)

    #             #### update groups 
    #             groups[ref]['sum_distinct'] = sum_distinct_sym
    #             groups[ref]['sum'] = sum_sym
    #             groups[ref]['max'] = max_sym
    #             groups[ref]['min'] = min_sym

    #     for fidx, func in enumerate(operator.agg_funcs):
    #         if func.this.key == 'star': continue
    #         ref = func.this.args.get('ref')
    #         distinct = func.args.get('distinct')
    #         key = func.key
    #         if key == 'avg':

    #             if distinct:
    #                 output.append(groups[ref]['sum_distinct'] / groups[ref]['count_distinct'])
    #             else:
    #                 output.append(groups[ref]['sum'] / groups[ref]['count'])
    #         else:
    #             if distinct:
    #                 key = key + '_distinct'
    #             output.append(groups[ref][key])
            
    #         if key in ['avg', 'sum']:
    #             self.path(groups[ref]['sum'] != groups[ref]['sum_distinct'], f'g{operator.i()}_group_sum', 'positive')
    #             self.path(groups[ref]['sum'] > 1, f'g{operator.i()}_group_sum', 'positive')

            
    #         self.path(groups[ref]['count'] != groups[ref]['count_distinct'], f'g{operator.i()}_group_count', 'positive')
    #         self.path(groups[ref]['count'] > 0, f'g{operator.i()}_group_count', 'positive')
    #         self.path(groups[ref]['count_distinct'] > 0, f'g{operator.i()}_group_count', 'positive')

    #     p.update(_id = operator.i(), data = [Row(expressions = output, multiplicity = 1)])
    #     return p

    # def _initialize_group_identifiers(self, aggfuncs, prefix):
    #     group = defaultdict(lambda: defaultdict(dict))
    #     for index, func in enumerate(aggfuncs):
    #         distinct = func.args.get('distinct')
    #         func_key = func.key if not distinct else f"{func.key}_distinct"
    #         func_dtype = str(func.args.get('datatype'))
    #         ref = func.this.args.get('ref') if func.this.key != 'star' else 0
            
    #         if ref in group and func_key in group[ref]:
    #             continue
    #         if func_key == 'avg': #  and func_key != 'avg'
    #             if 'count' not in group[ref]:
    #                 count_variable = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggf{ref}_{prefix}_count', value = None)
    #                 group[ref]['count'] = count_variable
    #             if 'sum' not in group[ref]:
    #                 sum_variable = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggf{ref}_{prefix}_sum', value = None)
    #                 group[ref]['sum'] = sum_variable
    #         else:
    #             variable = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggf{ref}_{prefix}_{func_key}', value = None)
    #             group[ref][func_key] = variable
    #     return group


    def _initialize_group_identifier(self, aggfuncs, row, prefix):

        group = defaultdict(dict)
        cardinality = defaultdict(lambda: defaultdict(list))
        for index, func in enumerate(aggfuncs):
            linput = self.execute(func.this, row = row)
            func_dtype = str(func.args.get('datatype'))
            distinct = func.args.get('distinct')
            func_key = func.key if not distinct else f"{func.key}_distinct"
            ref = func.this.args.get('ref')
            if ref in group and func_key in group[ref]:
                continue
            if func_key == 'count':
                group[ref]['count'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_count', value = 1)
                cardinality[ref]['count'].append(group[ref]['count'] == row.multiplicity)
            elif func_key == 'count_distinct':
                group[ref]['count_distinct'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_count_distinct', value = 1)
                cardinality[ref]['count_distinct'].append(group[ref]['count_distinct'] == 1)
            elif func_key == 'sum':
                group[ref]['sum'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_sum', value = 0)
                cardinality[ref]['sum'].append(group[ref]['sum'] == linput * row.multiplicity)
            elif func_key == 'sum_distinct':
                group[ref]['sum_distinct'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_sum_distinct', value = 0)
                cardinality[ref]['sum_distinct'].append(group[ref]['sum'] == linput)
            elif func_key == 'max':
                group[ref]['max'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_max', value = 0)
            elif func_key == 'min':
                group[ref]['min'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_min', value = 0)
            elif func_key == 'avg':
                if 'sum' not in group[ref]:
                    group[ref]['sum'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_sum', value = 0)
                    cardinality[ref]['sum'].append(group[ref]['sum'] == linput * row.multiplicity)
                if 'count' not in group[ref]:
                    group[ref]['count'] = ssa_factory.create_symbol(func_dtype, self.context, expr = f'aggfunc{index}_ref{ref}_{prefix}_count', value = 0)
                    cardinality[ref]['count'].append(group[ref]['count'] == row.multiplicity)
        return group, cardinality


    def execute_aggregate(self, operator: Step, **kwargs):
        if not operator.groupby:
            return self.execute_aggregate_to_single_group(operator, **kwargs)
        p = self.execute(operator.this, **kwargs)

        def get_groupkey(row):
            return tuple(row[column.args.get('ref')] for column in operator.groupby), row.multiplicity
        
        output = []
        exprs = []
        for rid, row, expr in zip(range(len(p.data)), p.data, p.expr):
            lgroup_key, mul = get_groupkey(row)
            identifier = f'g{operator.i()}_{rid}'
            group, cardinality = self._initialize_group_identifier(operator.agg_funcs, row, rid)
                
            ### group constraints
            c0 = expr.to_smt() > 0
            ### distinct count
            distinct_constraints = defaultdict(list)
            for func in operator.agg_funcs:
                linput =  self.execute(func.this, row = row)
                for jid in range(rid + 1, len(p.data)):
                    ref = func.this.args.get('ref')
                    rinput = self.execute(func.this, row = p.data[jid])
                    distinct_constraints[ref].append(linput != rinput)
            ## process agg funcs

            for jid in range(rid + 1, len(p.data)):
                rgroup_key, rmul = get_groupkey(p.data[jid])
                c1 = reduce(lambda x, y : x and y, tuple(lkey == rkey for lkey, rkey in zip(lgroup_key, rgroup_key)))

                for fidx, func in enumerate(operator.agg_funcs):
                    linput = self.execute(func.this, row = row)
                    rinput = self.execute(func.this, row = p.data[rid])
                    func_dtype = str(func.args.get('datatype'))
                    distinct = func.args.get('distinct')
                    func_key = func.key if not distinct else f"{func.key}_distinct"
                    ref = func.this.args.get('ref')
                    
                    c2 = linput.is_null().__not__()
                    c3 = rinput.is_null().__not__()

                    sym_identifier = f'{identifier}f{fidx}c{ref}{jid}_{func_key}'
                    sym = ssa_factory.create_symbol(func_dtype, self.context, expr = sym_identifier, value = None)
                    if func_key == 'count':
                        smt_expr = sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3,  group[ref].get(func_key) + 1,  group[ref].get(func_key))
                    elif func_key == 'sum':
                        smt_expr = sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3, group[ref][func_key] + rinput * rmul, group[ref][func_key])
                    elif func_key == 'max':
                        smt_expr = sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3 and rinput >= group[ref[func_key]], rinput, group[ref][func_key])
            
                    elif func_key == 'min':
                        smt_expr = sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3 and rinput <= group[ref[func_key]], rinput, group[ref][func_key])

                    elif func_key == 'count_distinct':
                        d1 = sum(distinct_constraints[ref])
                        smt_expr = sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3 and d1.to_smt() == len(distinct_constraints), group[ref][func_key] + 1,  group[ref][func_key])
            
                    elif func_key == 'sum_distinct':
                        d1 = sum(distinct_constraints[ref])
                        smt_expr = sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3 and d1.to_smt() == len(distinct_constraints), group[ref][func_key] + rinput * rmul,  group[ref][func_key])

                    if func_key == 'avg':
                        sum_sym = ssa_factory.create_symbol(func_dtype, self.context, expr = f'{identifier}f{fidx}c{ref}{jid}_sum', value = None)
                        cnt_sym = ssa_factory.create_symbol('int', self.context, expr = f'{identifier}f{fidx}c{ref}{jid}_count', value = None)

                        smt_expr1 = sum_sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3, group[ref]['sum'] + rinput * rmul, group[ref]['sum'])

                        smt_expr2 = cnt_sym == ssa_factory.ite(self.context, c0 and c1 and c2 and c3,  group[ref].get('count') + 1,  group[ref].get('count'))
                        group[ref]['sum'] = sum_sym
                        group[ref]['count'] = cnt_sym
                        cardinality[ref]['sum'].append(smt_expr1)
                        cardinality[ref]['count'].append(smt_expr2)
                    else:
                        group[ref][func_key] = sym
                        cardinality[ref][func_key].append(smt_expr)


            data = [*lgroup_key]
            for fidx, func  in enumerate(operator.agg_funcs):
                func_dtype = str(func.args.get('datatype'))
                distinct = func.args.get('distinct')
                ref = func.this.args.get('ref')
                if func.key != 'avg':
                    func_key = func.key if not distinct else f"{func.key}_distinct"
                    data.append(group[ref][func_key])
                    for c in cardinality[ref][func_key]:
                        self.path(Pred(this = c), operator.i() + f'group_agg_{fidx}_{func.key}', 'positive')
                else:
                    data.append(group[ref]['sum'] / group[ref]['count'])
                    for c in cardinality[ref]['sum']:
                        self.path(Pred(this = c), operator.i() + f'group_agg_{fidx}_{func.key}', 'positive')
                    for c in cardinality[ref]['count']:
                        self.path(Pred(this = c), operator.i() + f'group_agg_{fidx}_{func.key}', 'positive')
            r = Row(expressions = data, multiplicity = row.multiplicity)
            output.append(r)
            exprs.append(expr)
        p.update(_id = operator.i(), data = output, expr = exprs)
        return p



    def execute_scalar(self, operator: Step, **kwargs):
        p = self.execute(operator.this, **kwargs)

        if p.expr:
            expr = sum(p.expr)
            self.path(expr.to_smt() > 0, f'scalar_{operator.i()}', 'positive')
            self.path(expr.to_smt() <= 0, f'NEG_o{operator.i()}_scalar', 'negative')
        return p.data[0][0]

    def execute_union(self, operator: Step, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.right, **kwargs)

        output, exprs = [], []
        lexprs, rexprs = [], []

        for lrow, lexpr in zip(left.data, left.expr):
            output.append(lrow)
            exprs.append(lexpr)
            e_ = lexpr.to_smt() > 0
            lexprs.append(e_.__not__())
        for lrow, lexpr in zip(right.data, right.expr):
            output.append(lrow)
            exprs.append(lexpr)

            e_ = rexprs.to_smt() > 0
            rexprs.append(e_.__not__())

        self.path(ssa_factory.sall(*lexprs), f'NEG_o{operator.i()}_left_existence', 'negative')
        self.path(ssa_factory.sall(*rexprs), f'NEG_o{operator.i()}_right_existence', 'negative')
        
        p = Branch(_id = operator.i(), data = output, expr = exprs)
        return p
    


    
    def execute_neg(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.__neg__()
   
    def execute_not(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.__not__()

    def execute_or(self, operator: exp, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        return left.logical(right, 'or')
    
    def execute_and(self, operator, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        return left.logical(right, 'and')

    def execute_star(self, operator, **kwargs):
        row = kwargs.get('row')
        expr = row[0]
        if hasattr(expr, 'dtype'): self.context[4].add(expr.expr) 
        return expr

    def execute_column(self, operator,**kwargs):
        row = kwargs.get('row')
        expr = row[int(operator.args.get('ref'))]
        self.context[4].add(expr.expr)
        return expr

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

    def execute_is_null(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.is_null()
    
    def execute_cast(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this
    def execute_div(self, operator, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        result = left / right
        self.path(right != 0 , 'integrity', 'positive')
        return result

    def execute_like(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        fmt = self.execute(operator.expression, **kwargs)
        
        return this.like(fmt)

    def execute_in(self, operator, **kwargs):
        
        this = self.execute(operator.this, **kwargs)
        query = self.execute(operator.args.get('query'), **kwargs)

        output, smt_exprs = [], []
        for rrow, rexpr in zip(query.data, query.expr):
            c1 = this == rrow[0]
            smt_exprs.append(c1.logical(rexpr.to_smt() > 0, 'and'))

        return ssa_factory.sany(*smt_exprs)

    def execute_strftime(self, operator, **kwargs):
        fmt = self.execute(operator.args.get('format'), **kwargs)
        this = self.execute(operator.this, **kwargs)
        return this.strftime(fmt)
    
    def execute_date(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        e_ = this.expr
        v_ = this.value
        if this.dtype == 'String':
            e_ = z3.StrToInt(this.expr)
            try:
                v_ = int(v_)
            except Exception as e:
                v_ = None
        tt  =ssa_factory.create_symbol('date', context= self.context, expr = e_, value = v_)
        tt.format_ = '%Y-%m-%d'
        return tt

    def execute_substring(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        start = self.execute(operator.args.get('start'), **kwargs)
        length = self.execute(operator.args.get('length'), **kwargs)
        v = this.substring(start, length)
        return v 
    def execute_length(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.length

    def execute_case(self, operator, **kwargs):
        else_val = self.execute(operator.args.get('default'), **kwargs)
        for if_ in reversed(operator.args.get('ifs')):
            condition = self.execute(if_.this, **kwargs)
            true = self.execute(if_.args.get('true'), **kwargs)
            else_val = ssa_factory.ite(self.context, condition, true, else_val)
            self.path(condition, f'CASE{if_.this}', 'positive')

        return else_val
    def execute_abs(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this


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
    code += "   if parent.key != 'join':\n"
    code += "      self.path(Pred(this = result.__not__()), 'NEG_o' + parent.i() + '_' + str(operator), 'negative')\n"
    code += "   return result"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(NaiveExecutor, method, locals_dict[method])

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
    setattr(NaiveExecutor, method, locals_dict[method])

for (name, op) in binary_ops:
    method = "execute_%s" % name
    make_binary_method(method, op)
