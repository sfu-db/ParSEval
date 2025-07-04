from typing import Any, List, Dict, Optional, Tuple, Union
from dataclasses import dataclass, field, replace
from collections import defaultdict
from .constant import BranchType
from src.expression.symbol import Row, to_literal, and_, or_, Literal, get_all_variables, distinct, Strftime
from src.expression.visitors import get_predicates
from src.expression.query import rel
from .helper import split_sql_conditions, get_datatype, get_ref, get_refs
from sqlglot import exp
import logging

logger = logging.getLogger('src.parseval.executor')

@dataclass(frozen= True)
class InputRef:
    name: str
    index: int
    typ: str
    table: List[str] = field(default_factory= list)
    nullable: bool = field(default =False)
    unique: bool = field(default = False)
    is_computed: bool = False
    checks: List[Any] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)

@dataclass
class SymbolTable:
    _id: str
    data: List
    label: str = field(default = BranchType.POSITIVE, repr= False)
    row_expr: List = field(default_factory=list, repr= False)
    tbl_expr: List = field(default_factory=list, repr= False)    
    metadata: Dict = field(default_factory=dict, repr = False)


class Encoder:

    PREDEFINED = {}

    def __init__(self, add):
        self.add = add

    def __call__(self, root, instance, *args: Any, **kwds: Any) -> Any:
        return self.encode(root = root,  instance = instance)

    def encode(self, root, **kwargs) -> Any:
        try:
            fname = 'encode_%s' % root.key
            func = getattr(self, fname)
            return func(root, **kwargs)
        except AttributeError as e:
            raise NotImplementedError(f'Operator {root.key} is not implemented') from e
        except Exception as e:
            raise
    
    def update_metadata(self, *args):
        tables = []
        for arg in args:
            tables.extend(arg['table'])
        new_tables = []
        for new_index, ref in enumerate(tables):
            new_tables.append(replace(ref, index = new_index))
        return {'table': tables}
    
    def encode_scan(self, operator: rel.Step, **kwargs):
        '''
        we would not encode scan because of instance.
        '''
        instance = kwargs.get('instance')
        table = instance.get_table(operator.table)
        output = []
        for row in table:
            output.append(row)

        md = [InputRef(name= column.name, 
                       index= index, 
                       typ= column.kind, 
                       table= [operator.table], 
                       nullable= not table.is_notnull(column),
                       unique= table.is_unique(column)) for index, column in enumerate(table.column_defs)]
        st = SymbolTable(_id = operator.i(), data = output, metadata= {'table': md})
        return st
    
    def encode_project(self, operator, **kwargs):
        st = self.encode(operator.this, **kwargs)
        outputs, metadata, infos = [], [], []
        for projection in operator.projections:
            if isinstance(projection, exp.Column):
                inputref = st.metadata['table'][get_ref(projection)]
                metadata.append(inputref)
                infos.append({'table': [inputref]})
            elif isinstance(projection, exp.Literal):
                raise NotImplementedError('Literal is not implemented in Project')
            elif isinstance(projection, (exp.Div, exp.Mul, exp.Add, exp.Sub)):
                inputref = InputRef(name= projection.name, 
                                    index= len(metadata),
                                    typ= get_datatype(projection), 
                                    table = st.metadata['table'][get_ref(projection)].table, 
                                    nullable = False, unique= False, is_computed= True, 
                                    depends_on= [st.metadata['table'][ref] for ref in get_refs(projection.this)])
                # logger.info(f'project: {projection}, {inputref}')
                metadata.append(inputref)
                infos.append({'table': [inputref]})
            elif isinstance(projection, exp.Case):
                # Handle CASE expressions
                ref = get_ref(projection)
                
                # inputref = InputRef(name= projection.name, 
                #                     index= len(metadata), 
                #                     typ= get_datatype(projection), 
                #                     table = st.metadata['table'], 
                #                     nullable = False, unique= False, is_computed= True, 
                #                     depends_on= [st.metadata['table'][ref]])

                raise NotImplementedError('Case is not implemented in Project')
        
        for row in st.data:
            projections, takens = [], []
            for project in operator.projections:
                projections.append(self.encode(project, row = row))
                takens.append(True)
            outputs.append(Row(this = row.multiplicity, operands = projections))
            tuples = [row.this]
            self.add.which_branch(operator.key, operator.i(), projections, operator.projections, takens, 1, infos, tuples = tuples)

        self.add.advance(operator.key, operator.i())
        st = SymbolTable(operator.i(), data= outputs, metadata=  {'table': metadata})
        return st
    
    def encode_filter(self, operator: rel.Step, **kwargs):
        st = self.encode(operator.this, **kwargs)
        outputs = []
        for row in st.data:
            smt = self.encode(operator.condition, row = row, **kwargs)
            if smt:
                outputs.append(row)
            predicates = get_predicates(smt)
            tuples = [row.this]
            takens = [bool(p.value) for p in predicates]
            md = [st.metadata] * len(takens)
            sql_conditions = list(operator.condition.find_all(exp.Predicate))
            self.add.which_branch(operator.key, operator.i(), predicates, sql_conditions, takens, bool(smt.value), md, tuples = tuples)
        
        self.add.advance(operator.key, operator.i())
        st = SymbolTable(_id = operator.i(), data = outputs, metadata= st.metadata)
        return st


    def encode_join(self, operator: rel.Join, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.right, **kwargs)
        outputs, infos = [], []
        metadata = self.update_metadata(left.metadata, right.metadata)        
        join_type = operator.kind.lower()
        
        for l_row in left.data:
            predicates = []
            tuples = []
            for r_row in right.data:
                combined_row = l_row * r_row
                smt = self.encode(operator.condition, row=combined_row, **kwargs)                
                if smt:
                    outputs.append(combined_row)
                    tuples.append(combined_row.this)
                predicates.append(smt)
            predicate = or_(predicates)
            if join_type in ['inner']:
                takens = [predicate.value]
                self.add.which_branch(operator.key, operator.i(), [predicate], [ operator.condition], takens, predicate.value, [metadata], tuples = tuples)

            if join_type in ['left', 'full']:
                if predicate:
                    self.add.which_branch(operator.key, operator.i(), [predicate], [ operator.condition], [True], 1, [metadata], tuples = tuples)
                else:
                    null_row = [Literal.null(md.typ) for md in right.metadata['table']]
                    combined_row = Row(operands = [*l_row.operands, *null_row], this = l_row.this)
                    outputs.append(combined_row)
                    tuples.append(combined_row.this)
                    self.add.which_branch(operator.key, operator.i(), [predicate], [operator.condition], [False], 1, [metadata], tuples = tuples)
        
        if join_type == 'inner':
            for r_row in right.data:
                smt_exprs = []
                for l_row in left.data:
                    combined_row = l_row * r_row
                    smt = self.encode(operator.condition, row=combined_row, **kwargs)
                    smt_exprs.append(smt.not_())

                predicate = and_(smt_exprs)
                if predicate:
                    takens = [False]
                    tuples = [r_row.this]
                    self.add.which_branch(operator.key, operator.i(), [predicate], [operator.condition], takens, 0, [metadata], tuples = tuples)
        
        self.add.advance(operator.key, operator.i())
        st = SymbolTable(_id = operator.i(), data = outputs, metadata= metadata)
        return st
    
    def encode_union(self, operator: rel.Union, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.right, **kwargs)
        
        # Combine results
        outputs = left.data.copy()
        
        # If ALL is True, include duplicates, otherwise deduplicate
        if operator.args.get('all', False):
            outputs.extend(right.data)
        else:
            # Simple deduplication based on row content
            for r_row in right.data:
                if not any(str(l_row) == str(r_row) for l_row in outputs):
                    outputs.append(r_row)
        
        # Combine constraints
        smt_exprs = left.row_expr + right.row_expr
        op_exprs = left.tbl_expr + right.tbl_expr
        
        self.add.advance(operator.key, operator.i())
        return left.update(_id=operator.i(), data=outputs, row_expr=smt_exprs, tbl_expr=op_exprs)
    
    def encode_intersect(self, operator: rel.Intersect, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.right, **kwargs)
        
        outputs = []
        for l_row in left.data:
            for r_row in right.data:
                if str(l_row) == str(r_row):
                    outputs.append(l_row)
                    break
        
        # Combine constraints
        smt_exprs = left.row_expr + right.row_expr
        op_exprs = left.tbl_expr + right.tbl_expr
        
        self.add.advance(operator.key, operator.i())
        return left.update(_id=operator.i(), data=outputs, row_expr=smt_exprs, tbl_expr=op_exprs)
    
    def encode_minus(self, operator: rel.Minus, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.right, **kwargs)
        
        outputs = []
        for l_row in left.data:
            # Check if this row exists in right
            if not any(str(l_row) == str(r_row) for r_row in right.data):
                outputs.append(l_row)
        
        # Combine constraints
        smt_exprs = left.row_expr + right.row_expr
        op_exprs = left.tbl_expr + right.tbl_expr
        
        self.add.advance(operator.key, operator.i())
        return left.update(_id=operator.i(), data=outputs, row_expr=smt_exprs, tbl_expr=op_exprs)
    
    def encode_aggregate(self, operator: rel.Aggregate, **kwargs):
        st = self.encode(operator.this, **kwargs)
        metadata, infos, sql_conditions = [], [], []
        ## get metadata info
        for expr in operator.groupby:
            ref = get_ref(expr)
            datatype = get_datatype(expr) 
            inputref = InputRef(name= expr.name, index= len(metadata), typ=datatype, nullable= False, unique= True, table = st.metadata['table'][ref].table)
            metadata.append(inputref)
            infos.append({'table': [inputref], 'group_size': [], 'group_stats': []})
            sql_conditions.append(expr)

        for func in operator.agg_funcs:
            ref = get_ref(func)
            datatype = get_datatype(func) 
            inputref = InputRef(name= func.name, index= len(metadata), typ=datatype, nullable= False, unique= False, 
                                table = st.metadata['table'][ref].table,
                                is_computed= True, depends_on = [st.metadata['table'][ref]])
            metadata.append(inputref)
            infos.append({'table': [inputref], 'group_size': [], 'group_stats': []})
            sql_conditions.append(func)
        metadata = self.update_metadata({'table': metadata})

        # Group by the specified columns
        groups = {}
        tuples = []
        for row in st.data:
            group_key = tuple(self.encode(expr, row=row, **kwargs) for expr in operator.groupby)
            contain_group_key = False
            for existing_key, group_data in groups.items():
                if all(left == right for left, right in zip(existing_key, group_key)):
                    group_data.append(row)
                    contain_group_key = True
                    break
            if not contain_group_key:
                groups[group_key] = [row]
                tuples.append(row.this)


        group_keys = list(zip(*groups.keys()))
        group_count_predicates =  [distinct(list(col)) for col in group_keys]           ### process group by predicates

        takens = [True]  * len(group_count_predicates)
        self.add.which_branch(operator.key, operator.i(), group_count_predicates, sql_conditions[:len(group_count_predicates)], takens, 1, infos[:len(group_count_predicates)], tuples = tuples)

        agg_func_prediacates = [[]] * len(operator.agg_funcs)

        outputs = []
        for group_index, (group_key, group_rows) in enumerate(groups.items()):
            row_expressions = list(group_key)
            for func_index, agg_func in enumerate(operator.agg_funcs):
                agg_result,has_null, has_duplicate = self.encode(agg_func, rows=group_rows, **kwargs)
                row_expressions.append(agg_result)
                agg_func_prediacates[func_index].append(agg_result)
            outputs.append(Row(this = group_rows[0].this, operands = row_expressions))
            for info in infos:
                info['group_stats'].append((group_index, has_null, has_duplicate))
                info[f'group_size'].append((group_index, len(group_rows)))
            takens = [False]  * len(row_expressions)
            self.add.which_branch(operator.key, operator.i(), row_expressions, sql_conditions, takens, 1, infos, tuples = [tuples[group_index]])

        return SymbolTable(_id = operator.i(), data = outputs, metadata= metadata)
    
    def encode_sum(self, operator, **kwargs):
        """ Sum(this=Column(this=Identifier(this=$1, quoted=False), datatype=DataType(this=Type.FLOAT, nested=False)ref=1),
                distinct=False,
                datatype=DataType(this=Type.FLOAT, nested=False))
        """
        distinct = operator.args.get('distinct', False)
        rows = kwargs.get('rows')
        result = 0
        values = [self.encode(operator.this, row = row) for row in rows]
        concretes = set()
        has_null, has_duplicate = False, False
        for value in values:
            if value.is_null():
                has_null = True
                continue
            if value.value in concretes:
                has_duplicate = True
                if distinct:
                    continue
            concretes.add(value.value)
            result += value
        return result, has_null, has_duplicate
    
    def encode_count(self, operator, **kwargs):
        """Count
        """
        distinct = operator.args.get('distinct', False)
        rows = kwargs.get('rows')
        values = [self.encode(operator.this, row = row) for row in rows]
        has_null = any(value.is_null() for value in values)
        concretes = [v.value for v in values]
        has_duplicate = len(set(concretes)) != len(concretes)
        result = len(set(concretes)) if distinct else len(concretes)

        result = sum(values)
        # result = to_literal(result, to_type= 'int')s
        return result, has_null, has_duplicate
    
    def encode_star(self, operator, **kwargs):
        row = kwargs.get('row')
        return row[0]
        # logger.info(repr(operator))

    def encode_max(self, operator, **kwargs):
        """ Max(this=Column(this=Identifier(this=$1, quoted=False), datatype=DataType(this=Type.FLOAT, nested=False)ref=1),
                distinct=False,
                datatype=DataType(this=Type.FLOAT, nested=False))
        """
        distinct = operator.args.get('distinct', False)
        rows = kwargs.get('rows')
        result = values[0]
        values = [self.encode(operator.this, row = row) for row in rows]
        concretes = set()
        has_null, has_duplicate = False, False
        for value in values[1:]:
            if value.is_null():
                has_null = True
                continue
            if value.value in concretes:
                has_duplicate = True
                if distinct:
                    continue
            concretes.add(value.value)
            if value > result:
                result = value
        return result, has_null, has_duplicate

    def encode_min(self, operator, **kwargs):
        """ Max(this=Column(this=Identifier(this=$1, quoted=False), datatype=DataType(this=Type.FLOAT, nested=False)ref=1),
                distinct=False,
                datatype=DataType(this=Type.FLOAT, nested=False))
        """
        distinct = operator.args.get('distinct', False)
        rows = kwargs.get('rows')
        result = values[0]
        values = [self.encode(operator.this, row = row) for row in rows]
        concretes = set()
        has_null, has_duplicate = False, False
        for value in values[1:]:
            if value.is_null():
                has_null = True
                continue
            if value.value in concretes:
                has_duplicate = True
                if distinct:
                    continue
            concretes.add(value.value)
            if value < result:
                result = value
        return result, has_null, has_duplicate
        
    
    def encode_sort(self, operator: rel.Sort, **kwargs):
        def sorted_pure(iterable, key=None, reverse=False):
            def merge_sort(lst):
                if len(lst) <= 1:
                    return lst
                mid = len(lst) // 2
                left = merge_sort(lst[:mid])
                right = merge_sort(lst[mid:])
                return merge(left, right)

            def merge(left, right):
                result = []
                i = j = 0
                while i < len(left) and j < len(right):
                    a = key(left[i]) if key else left[i]
                    b = key(right[j]) if key else right[j]
                    if (a < b and not reverse) or (a > b and reverse):
                        result.append(left[i])
                        i += 1
                    else:
                        result.append(right[j])
                        j += 1
                result.extend(left[i:])
                result.extend(right[j:])
                return result
            return merge_sort(list(iterable))
        st = self.encode(operator.this, **kwargs)
        direction = operator.args.get('dir', 'ASCENDING')
        sort_keys = operator.args.get('sort')
        # Sort the rows
        outputs = sorted_pure(st.data, key = lambda row: tuple(row[sort_key['column']] for sort_key in sort_keys), reverse=('DESCENDING' in direction))
        offset = int(operator.offset) or 0
        limit = operator.limit or 100
        

        tuples = set()
        predicates = []
        sql_conditions = []
        metadata = self.update_metadata(st.metadata)

        for row in outputs:
            for sort_key in sort_keys:
                sql_condition = exp.to_column(f"${sort_key['column']}", ref = sort_key['column'], datatype = exp.DataType.build(dtype= sort_key.get('type')))  
                predicate = row[sort_key['column']]
                tuples = [row.this]

                self.add.which_branch(operator.key, operator.i(), [predicate], [sql_condition], [True], 1, [metadata], tuples = tuples)

        if offset > 0 or limit < 100:
            outputs = outputs[offset : offset + limit]
            
        st = SymbolTable(_id = operator.i(), data = outputs, metadata= st.metadata)
        self.add.advance(operator.key, operator.i())
        return st
        
        
    
    def encode_values(self, operator: rel.Values, **kwargs):
        # Create rows from the values
        values = operator.args.get('values', [])
        outputs = []
        
        for value_row in values:
            # Create a row with the specified values and multiplicity 1
            row = rel.Row(expressions=value_row, multiplicity=1)
            outputs.append(row)
        
        self.add.advance(operator.key, operator.i())
        return SymbolTable(_id=operator.i(), data=outputs, row_expr=[], tbl_expr=[])
    
    def encode_correlate(self, operator: rel.Correlate, **kwargs):
        # Execute the left input
        left = self.encode(operator.this, **kwargs)
        
        outputs = []
        for l_row in left.data:
            # For each row in the left input, execute the right input with correlation
            # This is a simplified implementation - actual correlation depends on your specific needs
            right = self.encode(operator.right, row=l_row, **kwargs)
            
            # Combine the results
            for r_row in right.data:
                combined_row = l_row * r_row
                outputs.append(combined_row)
        
        self.add.advance(operator.key, operator.i())
        return left.update(_id=operator.i(), data=outputs)


    def encode_neg(self, operator, **kwargs):
        this = self.encode(operator.this, **kwargs)
        return this.__neg__()
   
    def encode_not(self, operator, **kwargs):
        this = self.encode(operator.this, **kwargs)
        return this.not_()

    def encode_or(self, operator, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.expression, **kwargs)
        result = left.or_(right)
        # logger.info(f'or: {result}, {left.value} OR {right.value}, {result.value}')
        return left.or_(right)
    
    def encode_and(self, operator, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.expression, **kwargs)
        return left.and_(right)



    def encode_column(self, operator,**kwargs):
        row = kwargs.get('row')
        term = row[int(operator.args.get('ref'))]
        return term

    def encode_literal(self, operator, **kwargs):
        dtype = operator.args.get('datatype')
        return to_literal(operator.this, to_type= str(dtype)) 
    
    def encode_like(self, operator, **kwargs):
        this = self.encode(operator.this, **kwargs)
        expression = self.encode(operator.expression, **kwargs)
        # logger.info(this.like(expression))
        return this.like(expression)
    
    def encode_case(self, operator, **kwargs):
        # logger.info(repr(operator))
        else_val = self.encode(operator.args.get('default'), **kwargs)
        # case a > 20, b, when a > 10, b2,  else b3
        value = else_val
        for if_ in reversed(operator.args.get('ifs')):
            condition = self.encode(if_.this, **kwargs)
            true = self.encode(if_.args.get('true'), **kwargs)
            # if condition:
            #     else_val = true
            #     break
            else_val = condition.ite(true, else_val)
        # logger.info(else_val)
        return else_val
    
    def encode_cast(self, operator, **kwargs):
        """
        Casts a value to a specified type.
        """
        this = self.encode(operator.this, **kwargs)
        # datatype = operator.args.get('datatype')
        # logger.info(f"datatype: {datatype}, {this}")
        # logger.info(f"cast {repr(operator)}")
        # logger.info(this)
        to_type = operator.args.get('to')
        this = this.cast(to_type= str(to_type))
        # logger.info(this)
        return this

    def encode_is_null(self, operator, **kwargs):
        this = self.encode(operator.this, **kwargs)
        return this.is_null()
    
    def encode_strftime(self, operator, **kwargs):
        this = self.encode(operator.this, **kwargs)

        fmt = self.encode(operator.args.get('format'))
        from datetime import datetime
        try:
            from dateutil.parser import parse as dateutil_parse
            dt = dateutil_parse(this.value)
            value = datetime.strftime(dt, fmt.value)
        except Exception as e:
            value = None
        return Strftime(this = this, format = fmt, value = value)


ops =[
      ("gt", ">" ),\
      ("gte", ">="),\
      ("lt", "<"),\
      ("lte", "<="),\
      ("eq", "=="),\
      ("neq", "!=")]

def make_method(method, op):
    code = "def %s(self, operator, **kwargs):\n" % method
    code += "   left = self.encode(operator.this, **kwargs)\n"
    code += "   right = self.encode(operator.expression, **kwargs)\n"
    code += "   result = left %s right \n" % op
    code += "   return result"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(Encoder, method, locals_dict[method])

for (name, op) in ops:
    method = "encode_%s" % name
    make_method(method, op)

binary_ops = [    
    ('mul', '*'),
    ('add', '+'),
    ('sub', '-'),
    ('div', '/')
]

def make_binary_method(method, op):
    code = "def %s(self, operator, **kwargs):\n" % method
    code += "   left = self.encode(operator.this, **kwargs)\n"
    code += "   right = self.encode(operator.expression, **kwargs)\n"
    code += "   result = left %s right \n" % op
    code += "   return result"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(Encoder, method, locals_dict[method])

for (name, op) in binary_ops:
    method = "encode_%s" % name
    make_binary_method(method, op)

