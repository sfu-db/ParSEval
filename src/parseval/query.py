from __future__ import annotations
from sqlglot import parse_one, exp, MappingSchema
from sqlglot.optimizer import annotate_types, qualify
from typing import Dict, List, Callable, Optional, Any, Iterable, TYPE_CHECKING
from .states import raise_exception
from .dtype import DataType
    

class TypeInferencer:
    SPECULATIVE_TYPES = {
        exp.Cast: lambda  self, e: self._speculate_cast(e),
        exp.TimeToStr: lambda self, e: self._speculate_with_type(e.find(exp.Column), "DATETIME"),
        exp.TsOrDsToTimestamp: lambda self, e: self._speculate_with_type(e.find(exp.Column), "DATETIME"),
    }
    def __init__(self, mappingschema: MappingSchema, dialect: str):
        self.mappingschema = mappingschema
        self.dialect = dialect
        self.speculates: Dict[exp.Column, DataType] = {}
    def infer(self, expr: exp.Expression) -> Dict[exp.Column, DataType]:
        for node in expr.walk():
            rule = self.SPECULATIVE_TYPES.get(type(node))
            if rule:
                rule(self, node)
        return self.speculates
                
    def _set_type(self, expr, target_type):
        target_type = DataType.build(target_type)
        old_typ = self.speculates.get(expr)
        if old_typ is None:
            old_typ = expr.type
        
        self.speculates[expr] = self._unify(old_typ, target_type)

    def get(self, name: Any) -> Optional[DataType]:
        return self.speculates.get(name)
    
    def _unify(self, t1: DataType, t2: DataType) -> DataType:
        if t1 is None:
            return t2
        if t2 is None or t1.is_type(t2):
            return t1
        if t1.is_type(*DataType.TEMPORAL_TYPES):
            return t1
        if t2.is_type(*DataType.TEMPORAL_TYPES):
            return t2
        if t1.is_type(*DataType.TEXT_TYPES) or t2.is_type(*DataType.TEXT_TYPES):
            return t1
        if t1.is_type(*DataType.NUMERIC_TYPES) and t2.is_type(*DataType.NUMERIC_TYPES):
            if t1.is_type(*DataType.REAL_TYPES) and t2.is_type(*DataType.INTEGER_TYPES):
                return t1
            if t2.is_type(*DataType.REAL_TYPES) and t1.is_type(*DataType.INTEGER_TYPES):
                return t2
            return DataType.build("INT")
        if t1.is_type(*DataType.NUMERIC_TYPES) and t2.is_type(*DataType.INTEGER_TYPES):
            return t1
        return t2
    
    
        
    def _speculate_cast(self, expr: exp.Cast):
        to_type = expr.args.get("to")
        self._speculate_with_type(expr.this, to_type)
        
    def _speculate_with_type(self, expr: exp.Expression, target_type):
        if isinstance(expr, exp.Column):
            self._set_type(expr, target_type)


def _set_type(expr, target_type):
    target_type = DataType.build(target_type)
    
    old_typ = expr.type
    expr.type = _coerce(old_typ, target_type)
    
def _speculate_with_type( expr: exp.Expression, target_type):
    if isinstance(expr, exp.Column):
        _set_type(expr, target_type)
    return expr

def _speculate_cast(expr: exp.Cast):
    to_type = expr.args.get("to")
    return _speculate_with_type(expr.this, to_type)
    
        

SPECULATIVE_TYPES = {
        exp.Cast: lambda  e: _speculate_cast(e),
        exp.TimeToStr: lambda e: _speculate_with_type(e.find(exp.Column), "DATETIME"),
        exp.TsOrDsToTimestamp: lambda e: _speculate_with_type(e.find(exp.Column), "DATETIME"),
    }

def _coerce(t1: DataType, t2: DataType) -> DataType:
    if t1 is None:
        return t2
    if t2 is None or t1.is_type(t2):
        return t1
    if t1.is_type(*DataType.TEMPORAL_TYPES):
        return t1
    if t2.is_type(*DataType.TEMPORAL_TYPES):
        return t2
    if t1.is_type(*DataType.TEXT_TYPES) or t2.is_type(*DataType.TEXT_TYPES):
        return t1
    if t1.is_type(*DataType.NUMERIC_TYPES) and t2.is_type(*DataType.NUMERIC_TYPES):
        if t1.is_type(*DataType.REAL_TYPES) and t2.is_type(*DataType.INTEGER_TYPES):
            return t1
        if t2.is_type(*DataType.REAL_TYPES) and t1.is_type(*DataType.INTEGER_TYPES):
            return t2
        return DataType.build("INT")
    if t1.is_type(*DataType.NUMERIC_TYPES) and t2.is_type(*DataType.INTEGER_TYPES):
        return t1
    return t2
    
@raise_exception
def preprocess_sql(sql: str, mappingschema: MappingSchema, dialect: str, type_inferencer: Optional[Dict[exp.Expression, Callable]] = None) -> exp.Expression:
    """
    Preprocess the SQL query by parsing and re-serializing it to standardize formatting.

    Args:
        sql (str): The SQL query string to preprocess.
        mappingschema (MappingSchema): The schema mapping for table and column references.
        dialect (str): The SQL dialect to use for parsing.

    Returns:
        exp.Expression: The preprocessed SQL expression.
    """
    if type_inferencer is None:
        type_inferencer = TypeInferencer(mappingschema, dialect)
    parsed = parse_one(sql, dialect=dialect)
    tbls = list(parsed.find_all(exp.Table))
    def transform(node, tables):
        ## Transform SUM/COUNT/AVG with predicate into CASE WHEN structure
        if isinstance(node, (exp.Sum, exp.Count, exp.Avg)) and isinstance(node.this, exp.Predicate):
            whens = [exp.If(this = node.this, true = exp.Literal.number(1))]
            default = exp.Literal.number(0)
            node.set('this', exp.Case(ifs = whens, default = default))
            return node
        ## Transform quoted literal to unquoted
        if isinstance(node, exp.Column) and not node.table:
            for table in tables:
                if mappingschema.has_column(table, column=node, dialect=dialect, normalize=True):
                    node.set('table', exp.to_identifier(table.alias_or_name))
                    break            
            if not node.table:
                return exp.convert(node.this.this)
        if isinstance(node, (exp.Upper, exp.Lower)):
            return node.this
        return node
    parsed = parsed.transform(transform, tbls)
    parsed = qualify.qualify(parsed, schema=mappingschema, dialect= dialect)
    parsed = annotate_types.annotate_types(parsed, schema=mappingschema)
    
    # def speculate(node, rules):
    #     rule = rules.get(type(node))
    #     if rule:
    #         return rule(node)
    #     return node
    # parsed = parsed.transform(speculate, type_inferencer.SPECULATIVE_TYPES)
    return parsed


def infer_datatypes(expr: exp.Expression, mappingschema: MappingSchema, dialect: str, type_inferencer: Optional[TypeInferencer] = None) -> Dict[str, exp.DataType]:
    """
    Infer data types for columns in the given SQL expression.

    Args:
        expr (exp.Expression): The SQL expression to analyze.
        mappingschema (MappingSchema): The schema mapping for table and column references.
        dialect (str): The SQL dialect to use for parsing.

    Returns:
        Dict[str, exp.DataType]: A dictionary mapping column names to their inferred data types.
    """
    if type_inferencer is None:
        type_inferencer = TypeInferencer(mappingschema, dialect)
    return type_inferencer.infer(expr)

# class CoverageConstraints:
#     def __init__(self):
#         self.table_predicates = {}  # table -> Predicate
#         self.quantified_predicates = []
        
#     def add_table_predicate(self, table, predicate):
#         self.table_predicates.setdefault(table, []).append(predicate)

#     def add_quantified(self, qp):
#         self.quantified_predicates.append(qp)

# class SpeculativeAssigner:
#     def __init__(self, sql: str, catalog: Catalog2, dialect: str, limit: int = 100):
#         self.expr = parse_one(sql, dialect = dialect)
#         self.catalog = catalog
#         self.dialect = dialect
#         self.table_aliases: Dict[str, str] = {}
#         self.converage_constraints = CoverageConstraints()
        
#         context = {} # save all scopes' data
        
        
        ## process each scope to collect coverage constraints
        
        ## for each scope, insert data to cover coverage constraints
        
        ## if cannot cover current scope, backtrack to previous scope and insert more data
        
        ## repeat until all scopes are processed or reach limit
        
        
        
        
        
    
    
    
    
        
    
    