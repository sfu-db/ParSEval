from typing import Any, List, Dict, Optional
from dataclasses import dataclass, field
from .constant import BranchType
from src.expression.symbol import Row, to_literal, and_, or_, Literal, get_all_variables
from src.expression.visitors import get_predicates
from src.expression.query import rel
from .helper import split_sql_conditions
from sqlglot import exp
import logging

logger = logging.getLogger('src.parseval.executor')
@dataclass
class InputRef:
    #  {'kind': 'INPUT_REF', 'index': 1, 'name': '$1', 'type': 'VARCHAR'}
    name: str
    index: int
    typ: str
    table: str = field(default = None)
    nullable: bool = field(default =False)
    unique: bool = field(default = False)

    # def __repr__(self):
    #     return str(self)


@dataclass
class SymbolTable:
    _id: str
    data: List
    row_expr: List
    tbl_expr: List
    label: str = field(default = BranchType.POSITIVE, repr= False)
    metadata: Dict = field(default_factory=dict, repr = False)
    def update(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self
    def combine_constraints(self, other: 'SymbolTable') -> 'SymbolTable':
        """Combine constraints from another SymbolTable with this one."""
        self.row_expr.extend(other.row_expr)
        self.tbl_expr.extend(other.tbl_expr)
        return self
    
    def add_constraint(self, constraint, constraint_type: str = 'row'):
        """Add a constraint to the appropriate list."""
        if constraint_type == 'row':
            self.row_expr.append(constraint)
        elif constraint_type == 'table':
            self.tbl_expr.append(constraint)
        return self
    
    def set_branch_label(self, label: BranchType):
        """Set the branch label for this SymbolTable."""
        self.label = label
        return self
    
    def add_metadata(self, key: str, value: Any):
        """Add metadata to the SymbolTable."""
        self.metadata[key] = value
        return self
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata from the SymbolTable."""
        return self.metadata.get(key, default)

class Executor:
    def __init__(self, add):
        # self.instance = instance
        self.add = add

    def __call__(self, root, instance, *args: Any, **kwds: Any) -> Any:
        return self.execute(root = root,  instance = instance)

    def execute(self, root, **kwargs) -> Any:
        try:
            fname = 'execute_%s' % root.key
            func = getattr(self, fname)
            return func(root, **kwargs)
        except AttributeError as e:
            raise NotImplementedError(f'Operator {root.key} is not implemented') from e
        except Exception as e:
            raise
    
    def execute_scan(self, operator: rel.Step, **kwargs):
        '''
        we would not encode scan because of instance.
        '''
        instance = kwargs.get('instance')
        table = instance.get_table(operator.table)
        output, smt_exprs, op_exprs = [], [], []
        for row in table:
            output.append(row)

        metadata = [InputRef(name = col.name, 
                             index = index, 
                             typ = col.kind.this.name, 
                             table = operator.table, 
                             nullable = not table.is_notnull(col), 
                             unique = table.is_unique(col)) for index, col in enumerate(table.column_defs)]
        st = SymbolTable(_id = operator.i(), data = output, row_expr = smt_exprs, 
                         tbl_expr = op_exprs, metadata= {'table': metadata})
        return st
    
    def execute_project(self, operator, **kwargs):
        st = self.execute(operator.this, **kwargs)
        output, smt_exprs = [], []
        
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
                projections.append(self.execute(project, row = row))
            projections = [self.execute(project, row = row) for project in operator.projections]
            r = Row(this = row.multiplicity, operands = projections)
            output.append(r)
            tuples = get_all_variables(row.this)
            self.add.which_branch(operator.key, operator.i(), projections, operator.projections, [True] * len(projections), 1, st.metadata, tuples = tuples)

        self.add.advance(operator.key, operator.i())
        return st.update(_id = operator.i(), data = output, metadata = {'table': metadata})
    
    def execute_filter(self, operator: rel.Step, **kwargs):
        p = self.execute(operator.this, **kwargs)
        outputs, smt_exprs, op_exprs = [], [], []
        
        for row in p.data:
            tuples = set()
            smt = self.execute(operator.condition, row = row, **kwargs)
            if smt:
                outputs.append(row)
            predicates = get_predicates(smt)
            tuples.update(get_all_variables(row.this))
            takens = [p.value for p in predicates]
            self.add.which_branch(operator.key, operator.i(), predicates, split_sql_conditions(operator.condition), takens, smt.value, p.metadata, tuples = tuples)
        self.add.advance(operator.key, operator.i())
        return p.update(_id = operator.i(), data = outputs, expr = smt_exprs, op_exprs = op_exprs)


    def execute_join(self, operator: rel.Join, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.right, **kwargs)
        outputs, smt_exprs, op_exprs = [], [], []

        metadata = {'table': [*left.metadata['table'], *right.metadata['table']]}

        for new_index, ref in enumerate(metadata['table']):
            ref.index = new_index

        join_type = operator.kind.lower()
        for l_row in left.data:
            predicates = []
            for r_row in right.data:
                combined_row = l_row * r_row
                smt = self.execute(operator.condition, row=combined_row, **kwargs)                
                if smt:
                    outputs.append(combined_row)
                predicates.extend(get_predicates(smt))
            predicate = or_(predicates)
            if join_type in ['inner']:
                self.add.which_branch(operator.key, operator.i(), [predicate], [ operator.condition], [predicate.value], predicate.value, metadata)

            if join_type in ['left', 'full']:
                # logger.info(f"predicate: {predicate}")
                if predicate:
                    self.add.which_branch(operator.key, operator.i(), [predicate], [operator.condition], [True], predicate.value, metadata)
                else:
                    null_row = [Literal.null(md.typ) for md in right.metadata['table']]
                    combined_row = Row(operands = [*l_row.operands, *null_row], this = l_row.this)
                    outputs.append(combined_row)
                    self.add.which_branch(operator.key, operator.i(), [predicate], [exp.Not(this = operator.condition)], [True], 1, metadata)
        
        self.add.advance(operator.key, operator.i())
        return left.update(_id=operator.i(), data=outputs, row_expr=smt_exprs, tbl_expr=op_exprs, metadata = metadata)
    
    def execute_union(self, operator: rel.Union, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.right, **kwargs)
        
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
    
    def execute_intersect(self, operator: rel.Intersect, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.right, **kwargs)
        
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
    
    def execute_minus(self, operator: rel.Minus, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.right, **kwargs)
        
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
    
    def execute_aggregate(self, operator: rel.Aggregate, **kwargs):
        # First execute the input
        input_table = self.execute(operator.this, **kwargs)
        
        # Group by the specified columns
        groups = {}
        for row in input_table.data:
            # Evaluate group by expressions
            group_key = tuple(self.execute(expr, row=row, **kwargs) for expr in operator.groupby)
            
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
                    agg_result = self.execute(agg_func, rows=group_rows, **kwargs)
                    row_expressions.append(agg_result)
            
            # Create a new row with multiplicity 1 (aggregate result)
            new_row = rel.Row(expressions=row_expressions, multiplicity=1)
            outputs.append(new_row)
        
        # Track constraints
        smt_exprs = input_table.row_expr
        op_exprs = input_table.tbl_expr
        
        self.add.advance(operator.key, operator.i())
        return input_table.update(_id=operator.i(), data=outputs, row_expr=smt_exprs, tbl_expr=op_exprs)
    
    def execute_sort(self, operator: rel.Sort, **kwargs):
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
        # Execute the input
        input_table = self.execute(operator.this, **kwargs)
        # Sort the data based on the specified direction
        direction = operator.args.get('dir', 'ASCENDING')
        sort_keys = operator.args.get('sort')
        # Sort the rows
        sorted_data = sorted(
            input_table.data,
            key=lambda row: (row[sort_key] for sort_key in sort_keys),
            reverse=('DESCENDING' in direction)
        )
        
        # Apply offset and limit if specified
        offset = int( operator.offset) or 0
        limit = float(operator.limit) or float('inf')
        
        if offset > 0 or limit < float('inf'):
            sorted_data = sorted_data[offset:offset+limit]
        
        # self.add.advance(operator.key, operator.i())
        return input_table.update(_id=operator.i(), data=sorted_data)
    
    def execute_values(self, operator: rel.Values, **kwargs):
        # Create rows from the values
        values = operator.args.get('values', [])
        outputs = []
        
        for value_row in values:
            # Create a row with the specified values and multiplicity 1
            row = rel.Row(expressions=value_row, multiplicity=1)
            outputs.append(row)
        
        self.add.advance(operator.key, operator.i())
        return SymbolTable(_id=operator.i(), data=outputs, row_expr=[], tbl_expr=[])
    
    def execute_correlate(self, operator: rel.Correlate, **kwargs):
        # Execute the left input
        left = self.execute(operator.this, **kwargs)
        
        outputs = []
        for l_row in left.data:
            # For each row in the left input, execute the right input with correlation
            # This is a simplified implementation - actual correlation depends on your specific needs
            right = self.execute(operator.right, row=l_row, **kwargs)
            
            # Combine the results
            for r_row in right.data:
                combined_row = l_row * r_row
                outputs.append(combined_row)
        
        self.add.advance(operator.key, operator.i())
        return left.update(_id=operator.i(), data=outputs)


    def execute_neg(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.__neg__()
   
    def execute_not(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
        return this.not_()

    def execute_or(self, operator, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        result = left.or_(right)
        # logger.info(f'or: {result}, {left.value} OR {right.value}, {result.value}')
        return left.or_(right)
    
    def execute_and(self, operator, **kwargs):
        left = self.execute(operator.this, **kwargs)
        right = self.execute(operator.expression, **kwargs)
        return left.and_(right)



    def execute_column(self, operator,**kwargs):
        row = kwargs.get('row')
        term = row[int(operator.args.get('ref'))]
        return term

    def execute_literal(self, operator, **kwargs):
        dtype = operator.args.get('datatype')
        return to_literal(operator.this, to_type= str(dtype)) 

    def execute_is_null(self, operator, **kwargs):
        this = self.execute(operator.this, **kwargs)
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
    code += "   left = self.execute(operator.this, **kwargs)\n"
    code += "   right = self.execute(operator.expression, **kwargs)\n"
    code += "   result = left %s right \n" % op
    code += "   return result"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(Executor, method, locals_dict[method])

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
    setattr(Executor, method, locals_dict[method])

for (name, op) in binary_ops:
    method = "execute_%s" % name
    make_binary_method(method, op)

