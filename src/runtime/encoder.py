from typing import Any, List, Dict, Optional
from dataclasses import dataclass, field, replace
from .constant import BranchType
from src.expression.symbol import Row, to_literal, and_, or_, Literal, get_all_variables, distinct
from src.expression.visitors import get_predicates
from src.expression.query import rel
from .helper import split_sql_conditions
from sqlglot import exp
import logging

logger = logging.getLogger('src.parseval.executor')
@dataclass(frozen= True)
class InputRef:
    name: str
    index: int
    typ: str
    table: str = field(default = None)
    nullable: bool = field(default =False)
    unique: bool = field(default = False)

@dataclass
class SymbolTable:
    _id: str
    data: List
    label: str = field(default = BranchType.POSITIVE, repr= False)
    row_expr: List = field(default_factory=list, repr= False)
    tbl_expr: List = field(default_factory=list, repr= False)    
    metadata: Dict = field(default_factory=dict, repr = False)


class Encoder:
    def __init__(self, add):
        # self.instance = instance
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

        metadata = [InputRef(name = col.name, 
                             index = index, 
                             typ = col.kind.this.name, 
                             table = operator.table, 
                             nullable = not table.is_notnull(col), 
                             unique = table.is_unique(col)) for index, col in enumerate(table.column_defs)]
        st = SymbolTable(_id = operator.i(), data = output, metadata= {'table': metadata})
        return st
    
    def encode_project(self, operator, **kwargs):
        st = self.encode(operator.this, **kwargs)
        outputs = []
        
        metadata = []
        for projection in operator.projections:
            if isinstance(projection, exp.Column):
                ref = int(projection.args.get('ref'))
                metadata.append(st.metadata['table'][ref])
            elif isinstance(projection, exp.Literal):
                raise NotImplementedError('Literal is not implemented in Project')
            elif isinstance(projection, exp.Case):
                raise NotImplementedError('Case is not implemented in Project')
        for row in st.data:
            projections = []
            for project in operator.projections:
                projections.append(self.encode(project, row = row))
            projections = [self.encode(project, row = row) for project in operator.projections]
            outputs.append(Row(this = row.multiplicity, operands = projections))
            tuples = [row.this]
            taken = [True] * len(projections)
            self.add.which_branch(operator.key, operator.i(), projections, operator.projections, taken, 1, st.metadata, tuples = tuples)
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
            tuples = set([row.this])
            # get_all_variables(row.this)
            takens = [p.value for p in predicates]
            node = self.add.which_branch(operator.key, operator.i(), predicates, split_sql_conditions(operator.condition), takens, smt.value, st.metadata, tuples = tuples)
            
        self.add.advance(operator.key, operator.i())
        st = SymbolTable(_id = operator.i(), data = outputs, metadata= st.metadata)
        return st


    def encode_join(self, operator: rel.Join, **kwargs):
        left = self.encode(operator.this, **kwargs)
        right = self.encode(operator.right, **kwargs)
        outputs = []
        metadata = self.update_metadata(left.metadata, right.metadata)        
        join_type = operator.kind.lower()
        for l_row in left.data:
            predicates = []
            tuples = set()
            for r_row in right.data:
                combined_row = l_row * r_row
                smt = self.encode(operator.condition, row=combined_row, **kwargs)                
                if smt:
                    outputs.append(combined_row)
                    tuples.add(combined_row.this)
                predicates.append(smt)
            predicate = or_(predicates)
            if join_type in ['inner']:
                takens = [predicate.value]
                self.add.which_branch(operator.key, operator.i(), [predicate], [ operator.condition], takens, predicate.value, metadata, tuples = tuples)
            if join_type in ['left', 'full']:
                if predicate:
                    self.add.which_branch(operator.key, operator.i(), [predicate], [ operator.condition], [True], 1, metadata, tuples = tuples)
                else:
                    null_row = [Literal.null(md.typ) for md in right.metadata['table']]
                    combined_row = Row(operands = [*l_row.operands, *null_row], this = l_row.this)
                    outputs.append(combined_row)
                    tuples.add(combined_row.this)
                    self.add.which_branch(operator.key, operator.i(), [predicate], [operator.condition], [False], 1, metadata, tuples = tuples)
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
        # First execute the input
        st = self.encode(operator.this, **kwargs)
        metadata = []
        for index, expr in enumerate(operator.groupby):
            
            ref = int(expr.args.get('ref'))
            
            metadata.append(InputRef(name= expr.name, index= index, typ= expr.args.get("datatype").this.name, nullable= False, unique= True, table = st.metadata['table'][ref].table))
        
        for agg_func in operator.agg_funcs:
            metadata.append(InputRef(name= agg_func.name, index= 0, typ= agg_func.type, nullable = False, unique= False, table = st.metadata['table'][agg_func.index].table))

        # Group by the specified columns
        groups = {}
        for row in st.data:
            # Evaluate group by expressions
            group_key = tuple(self.encode(expr, row=row, **kwargs) for expr in operator.groupby)
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(row)
        
        # Apply aggregate functions to each group
        outputs = []
        for group_key, group_rows in groups.items():
            # Create a new row with group key values and aggregate results
            row_expressions = list(group_key)
            # Apply aggregate functions if specified
            if operator.agg_funcs:
                for agg_func in operator.agg_funcs:
                    # Evaluate aggregate function on the group
                    agg_result = self.encode(agg_func, rows=group_rows, **kwargs)
                    row_expressions.append(agg_result)
            
            # Create a new row with multiplicity 1 (aggregate result)
            outputs.append(Row(this = row.multiplicity, operands = row_expressions))
            # rel.Row(expressions=row_expressions, multiplicity=1)
            
        
        # Track constraints
        
        st = SymbolTable(_id = operator.i(), data = outputs, metadata= self.update_metadata({'table': metadata}))
        return st
        
    
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
        for sort_key in sort_keys:
            smt_exprs = []
            for row in outputs:
                smt_exprs.append(row[sort_key['column']])
                tuples.add(row.this)
            predicates.append(or_([smt_exprs[i] != smt_exprs[j] for i in range(len(smt_exprs)) for j in range(i+1, len(smt_exprs))]))            
            sql_conditions.append(exp.to_column(f"${sort_key['column']}", datatype = exp.DataType.build(dtype= sort_key.get('type'))))
        metadata = self.update_metadata(st.metadata)
        self.add.which_branch(operator.key, operator.i(), predicates, sql_conditions, [True] * len(sort_keys), 1, metadata, tuples = tuples)

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

    def encode_is_null(self, operator, **kwargs):
        this = self.encode(operator.this, **kwargs)
        return this.is_null()

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
    ('sub', '-')
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

