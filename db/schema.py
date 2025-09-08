
from __future__ import annotations
import sqlglot.generator
import ast, re, z3, sqlglot, random, logging
from sqlglot import expressions as exp
from sqlglot.helper import name_sequence
from collections.abc import Iterator
from typing import List, Dict, Any, Optional, Union, Set, Sequence, TypeVar, Generic, Tuple, Generator
from collections import defaultdict, deque
from parseval.symbol import Term
from parseval.query import uexpr
from parseval.disjoint_set import DisjointSet
from parseval import datatypes as dt_typ
from parseval.data_generator.solver import Solver
from .connection import Connection

logger = logging.getLogger('app')

## For JSON FORMAT
TABLE_NAME = 'table_name'
COLUMNS = 'columns'
    
_TP = TypeVar("_TP", bound=Tuple[Term, ...])


def get_fk_from_column(expression: exp.ForeignKey):
    dep_table = expression.args.get('reference').find(exp.Schema)
    dep_table_name = dep_table.this.name
    dep_table_column_name = dep_table.expressions[0].name
    return expression.expressions[0].this, dep_table_name, dep_table_column_name


class RowIterator(Iterator):
    _position: int = None
    _reverse: bool = False
    def __init__(self, collection: Table, reverse: bool = False) -> None:
        super().__init__()
        self._collection = collection
        self._reverse = reverse
        self._position = -1 if reverse else 0
    def __next__(self) -> Any:
        try:
            value = self._collection[self._position]
            self._position += -1 if self._reverse else 1
        except IndexError:
            raise StopIteration()
        return value

class Row(Sequence[Any], Generic[_TP]):
    row_id: int
    def _tuple(self) -> _TP:
        return self
    
class Table:
    __key__ = 'TABLE'
    @classmethod
    def from_ddl(cls, ddl: exp.Expression):
        assert ddl.kind == 'TABLE', f'Cannot initialize database instance based on ddl : {ddl}'
        assert isinstance(ddl, exp.Create), f'Cannot initialize database instance based on ddl : {ddl}'
        schema_obj = ddl.this
        primary_key = []
        foreign_keys = []
        column_defs = []
        for column_def in schema_obj.expressions:
            if isinstance(column_def, exp.ColumnDef):
                for constraint in column_def.constraints[:]:
                    if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                        column_def.constraints.remove(constraint)
                        if column_def.this not in primary_key: primary_key.append(column_def.this)
                    elif isinstance(constraint.kind, exp.AutoIncrementColumnConstraint):
                        column_def.constraints.remove(constraint)
                column_defs.append(column_def)
            elif isinstance(column_def, exp.PrimaryKey):
                primary_key.extend([item for item in column_def.expressions if item not in primary_key])
            elif isinstance(column_def, exp.ForeignKey):
                foreign_keys.append(column_def)
        pk = exp.PrimaryKey(expressions = list(primary_key))
        return cls(name = schema_obj.this.name, column_defs = tuple(column_defs), primary_key = pk, foreign_keys = foreign_keys)
    
    def __init__(self, name:str, column_defs: List[exp.ColumnDef] | None = None, data: List[Any] = None, **kw) -> None:
        self.name = name
        self.column_defs: List[exp.ColumnDef] = column_defs if column_defs is not None else []
        self.data = data if data is not None else []
        self.tuples = []
        self.primary_key: exp.PrimaryKey = kw.get('primary_key', exp.PrimaryKey(expressions = []))
        self.foreign_keys: List[exp.ForeignKey] = kw.get('foreign_keys', [])

    @property
    def shape(self):
        return (len(self.data), len(self.column_defs))
    def __getitem__(self, index :int) -> List[Term]:
        row = self.data[index]
        return [row[col.name] for col in self.column_defs]
    def __iter__(self):
        return RowIterator(self)
    def get_column_data(self, column_name) -> List[Term]:
        return [row[column_name] for row in self.data]
    
    def get_column_datatype(self, column: Union[str, int]) -> exp.DataType:
        return self.get_column(column).kind
    
    def is_unique(self, column: Union[str, int]) -> bool:
        column_def = self.get_column(column)
        if column_def.constraints and any(isinstance(constraint.kind, (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint)) for constraint in column_def.constraints):
            return True
        if column_def.this in self.primary_key.expressions:
            return True
        return False
    
    def is_notnull(self, column: Union[str, int]) -> bool:
        column_def = self.get_column(column)
        if any(isinstance(constraint.kind, (exp.NotNullColumnConstraint, exp.PrimaryKeyColumnConstraint)) for constraint in column_def.constraints):
            return True
        if column_def.this in self.primary_key.expressions:
            return True
        if any(column_def.this in k.expressions for k in self.foreign_keys):
                return True
        return False

    def get_column(self, column: Union[str, int]) -> exp.ColumnDef:
        '''return Column named `column_name` '''
        if isinstance(column, int):
            return self.column_defs[column]
        for c in self.column_defs:
            if c.name == column:
                return c
        raise RuntimeError(f'There is no columns named {column} in table {self.name}')
    def get_column_index(self, column: Union[str, exp.Identifier]) -> int:
        for cidx, column_def in enumerate(self.column_defs):
            if str(column) == column_def.name:
                return cidx
        raise RuntimeError(f'Could not find column named {column} in table {self.name}')
    
    def get_tuples(self) -> List[uexpr.UTuple]:
        return self.tuples
    # self._get_tuples()
    
    def _get_tuples(self):
        for tup in self.tuples:
            yield tup
        

    def append(self, row: Dict):
        self.data.append(row)

    def reset(self):
        self.data.clear()

    def sql(self, dialect = 'sqlite'):
        keywords = ["year", "date", "matches", "language", "result", "show", "time", 'power', 'element' , 'free', 'month', 'user', \
                              'count', 'position', 'match', 'member', 'translation', 'rank', 'percent', 'timestamp']
        exprs = []
        for c in self.column_defs:
            t = c.copy()
            if t.name.lower() in keywords:
                t.args['quoted'] = True
            if dialect == 'mysql' and c.kind.is_type(exp.DataType.Type.DATETIME):
                t.find(exp.DataType).replace(exp.DataType.build('TIMESTAMP'))
            exprs.append(t)
        # exprs = [c.sql(dialect= dialect) for c in self.column_defs]
        if self.primary_key and self.primary_key.expressions:
            exprs.append(self.primary_key)        
        exprs.extend(self.foreign_keys)
        obj = exp.Create(this = exp.Schema(this = exp.Table(this = f'`{str(self.name)}`'), expressions = exprs), exists = True, kind = 'TABLE')
        return obj.sql(dialect= dialect, identify = True)

class Instance:
    @classmethod
    def from_ddl(cls,  sql, name = 'public', dialect = None, ctx = None) -> Instance:
        # ctx = z3.Context()
        ddls = sqlglot.parse(sql, dialect = dialect)
        tables = {}
        foreign_keys = {}
        for ddl in ddls:
            tbl = Table.from_ddl(ddl=ddl)
            tables[tbl.name] = tbl
            foreign_keys[tbl.name] = tbl.foreign_keys
        return cls(name, tables, foreign_keys = foreign_keys, dialect = dialect, ctx = ctx)
    
    def __init__(self, name, tables: Dict[str, Table] | None = None, **kw) -> None:
        self.name = name
        self.context = ({}, DisjointSet(), defaultdict(set), {}, {}) ## variables definition, row level constraints, cell <-> table, column mapping, DB constraints
        self.foreign_keys: Dict[str, List[exp.ForeignKey]] = kw.get('foreign_keys', {})
        self._tables: Dict[str, Table] = tables
        self.ctx = kw.get('ctx', z3.Context())

        self.tsequence = {}
    
    def get_table(self, table_name) -> Table:
        return self._tables[table_name]
    
    def get_column_datatype(self, table_name, column: Union[str, int]) -> exp.DataType:
        table = self.get_table(table_name)
        return table.get_column(column).kind

    def is_column_unique(self, table_name, column: Union[str, int]) -> bool:
        table = self.get_table(table_name)
        return table.is_unique(column)
        
    def is_column_notnull(self, table_name, column: Union[str, int]) -> bool:
        table = self.get_table(table_name)
        return table.is_notnull(column)
    

    def topo_tables(self):
        visited = {k: False for k in self._tables.keys()}
        q = deque([table_name for table_name in list(self._tables.keys() ) if table_name not in list(self.foreign_keys.keys()) or not self.foreign_keys.get(table_name, [])])
        topo_order = []
        while q:
            table_name = q.popleft()
            topo_order.append(table_name)
            visited[table_name] = True
            for tbl_name, fks in self.foreign_keys.items():
                if visited[tbl_name]:
                    continue
                if all([fk.args.get('reference').find(exp.Table).name == tbl_name or visited[fk.args.get('reference').find(exp.Table).name] for fk in fks ]):
                    if tbl_name not in q: q.append(tbl_name)        
        return [self._tables[tbl_name] for tbl_name in topo_order]

    def tuple_sequence(self, table_name):
        if table_name not in self.tsequence:
            self.tsequence[table_name] = name_sequence(f"{table_name}_t_")
        return self.tsequence[table_name]()
    

    def append_tuple(self, table_name) -> uexpr.UTuple:
        table = self.get_table(table_name)
        tuple_name = self.tuple_sequence(table_name)
        self.context[3][tuple_name] = table_name
        tup = uexpr.UTuple(this = tuple_name, expressions = [], table = table, count = uexpr.Relation(this = tuple_name, table = table_name))
        for cidx, column_def in enumerate(table.column_defs):
            z_name = "%s_%s_%s_%s" % (table_name, column_def.name, column_def.kind.name, tup.this)
            z_value = Term.create(dtype= column_def.kind, z_name= z_name, v = None, ctx= self.ctx)
            column_ref = exp.Column(this = column_def.name, ref = cidx, table = table_name, datatype = column_def.kind, t = str(tup.this), term = z_value)
            tup.append('expressions', column_ref)
            self.context[4][str(z_value.symbol)] = (table_name, column_def.name)
        table.tuples.append(tup)
        return tup


    def seeds(self, **kwargs):
        '''
            Initialize instances with size : t, and generate tuples
        '''
        self.seed(kwargs.get('size'))

    def seed(self, size = 2):
        '''
            Generate a seed instance with size :arg
        '''
        for table_name in self._tables:
            for _ in range(size):
                self.append_tuple(table_name)
                

    


    def get_database_constraints(self) -> List:
        exprs = []
        for table_name in self._tables:
            table = self.get_table(table_name)
            for expr in table.primary_key.expressions:
                column_data = table.get_column_data(expr.name)
                exprs.append(z3.Distinct(*[x.symbol for x in column_data]))
            for fk in table.foreign_keys:
                from_column, to_table_name, to_column_name = get_fk_from_column(fk)
                from_data = [x.symbol for x in table.get_column_data(from_column)]
                to_table = self.get_table(to_table_name)
                to_data = [x.symbol for x in to_table.get_column_data(to_column_name)]
                exprs.append(z3.ForAll(from_data, z3.Exists(to_data, z3.Or([from_data[i] == to_data[j]  for j in range(len(to_data)) for i in range(len(from_data))]))))
        return exprs
    def update_concrete(self, *constraints):
        solver = Solver(self.ctx)

        paths = [*constraints]
        paths.extend(self.get_database_constraints())
        for _, item in self.context[1].items():
            paths.extend(item)
        
        assignments = solver.get_all_values(*paths)
        if not assignments:
            return
        for key, value in assignments.items():
            if key not in self.context[0]:
                continue
            print(f'set {self.context[0][key]} from {self.context[0][key].concrete} to {value}')
            self.context[0][key].concrete = value

    def initialize_instance(self, assignments: Dict):
        
        for tuple_name, assign in assignments.items():
            multipliticy = assign.get('multipliticy')
            concretes = {self.context[4][k][1]: v for k, v in assign.get('assignments').items()}
            for _ in range(multipliticy):

                self.insert(self.context[3][tuple_name], concretes)

    def add_constraint(self, smt_expr: Union[Term, z3.ExprRef], exclusion = []):
        constrinat = smt_expr.symbol if isinstance(smt_expr, Term) else smt_expr
        self.context[1].add(constrinat)
        
        # for vari in get_all_vars(constrinat):
        #     if str(vari) not in self.path:
        #         self.path[str(vari)] = []
        #     if str(vari) not in exclusion:
        #         self.path[str(vari)].append(constrinat)

    # def generate_next(self, column: rel.Column, existings: List, refer_column: rel.Column ):
    #     next_val = next(DEFAULT_VALUE[dt.normalize(column.dtype)](column.name, column.dtype, column.primary_key or column.unique))
    #     if column.unique or column.primary_key or len(existings) < 2:
    #         return next_val
    #     group_data = {}
    #     for item in existings:
    #         group_data[item.concrete] = group_data.get(item.concrete, 0) + 1

    #     # unique_values = len(group_data) #set(filter(lambda x: x is not None, existings))
    #     num_unique = len(group_data)
    #     num_total = len(existings)
    #     num_nulls = len(list(filter(lambda x: x.is_null(), existings)))
    #     num_duplicates = num_total - num_unique - num_nulls
    #     # Calculate current ratios
    #     current_null_ratio = num_nulls / num_total if num_total > 0 else 0
    #     current_unique_ratio = num_unique / num_total if num_total > 0 else 1
    #     current_duplicate_ratio = num_duplicates / num_total if num_total > 0 else 0

    #     deviations = {
    #         'unique' : current_unique_ratio - get_config().UNIQUE_RATIO,
    #         'duplicate' : current_duplicate_ratio - get_config().DUPLICATE_RATIO
    #     }
    #     if refer_column is None or not column.notnull:
    #         deviations['null'] = current_null_ratio - get_config().NULL_RATIO
    #     weights = {key: -deviations[key] if deviations[key] < 0 else 0 for key in deviations}
    #     total_weight = sum(weights.values())
    #     if total_weight == 0:
    #         next_val_typ = random.choice(list(deviations.keys()))
    #     else:
    #         normalized_weights = {key: weight / total_weight for key, weight in weights.items()}
    #         next_val_typ = random.choices(list(normalized_weights.keys()), weights=normalized_weights.values())[0]
    #     next_vals = {
    #         'unique': next_val,
    #         'duplicate': random.choice([value for value in existings if not value.is_null()]).concrete if [value for value in existings if not value.is_null()] else next_val,
    #         'null': NULL()
    #     }
    #     return next_vals[next_val_typ]

    def insert(self, table_name, concretes):
        table = self.get_table(table_name= table_name)
        row = {}
        ret = []
        for column_def in table.column_defs:
            z_name = "%s_%s_%s_%s" % (table.name, column_def.name, column_def.kind.name, table.shape[0])
            
            refer_column = None
            # for fk in self.foreign_keys:
            #     if fk.from_tbl == table_name and fk.from_col == column_def.name:
            #         refer_column = self.get_table(table_name= fk.to_tbl).get_column(fk.to_col)
            # nx_val = self.generate_next(column= column, existings= table.get_column_data(column.name), refer_column= refer_column)
            nx_val = None
            concrete = concretes.get(column_def.name, nx_val)
            z_value = Term.create(dtype= column_def.kind, z_name= z_name, v = concrete, ctx= self.ctx)
            row[column_def.name] = z_value
            ret.append(z_value)
            self.context[0][str(z_value.symbol)] = z_value
        table.append(row)
        return ret

    def preprocess(self):
        # unify data types
        for foreignkey in self.foreign_keys:
            from_tbl = foreignkey.from_tbl
            from_col = foreignkey.from_col
            to_tbl = foreignkey.to_tbl
            to_col = foreignkey.to_col
            from_dtype = dt_typ.normalize(self.get_table(from_tbl).get_column(from_col).dtype)
            to_dtype = dt_typ.normalize(self.get_table(to_tbl).get_column(to_col).dtype)
            self.get_table(from_tbl).get_column(from_col).args['notnull'] = True
            if from_dtype != to_dtype:
                if 'Int' in [from_dtype, to_dtype]:
                    if from_dtype == 'Int':
                        self.get_table(to_tbl).get_column(to_col).args['dtype'] = self.get_table(from_tbl).get_column(from_col).dtype
                    if to_dtype == 'Int':
                        self.get_table(from_tbl).get_column(from_col).args['dtype'] = self.get_table(to_tbl).get_column(to_col).dtype
        

    def postprocess(self):
        try:
            rows = {}
            for foreignkey in self.foreign_keys:
                from_table = foreignkey.from_tbl
                to_table = foreignkey.to_tbl
                rows[to_table] = []
                for record in self.get_table(from_table).get_column_data(foreignkey.from_col):
                    mark = False
                    for ref in self.get_table(to_table).get_column_data(foreignkey.to_col):
                        if record == ref:
                            mark = True
                    if not mark:
                        self.insert(to_table, concretes= {foreignkey.to_col : record.concrete})
                        mark = True
            # negative branch of foreign key (i.e. a record in to not in from)
            # for foreignkey in self.foreign_keys:
            #     from_table = foreignkey.from_tbl
            #     to_table = foreignkey.to_tbl
            #     mark = False
            #     for record in self.get_table(to_table).get_column_data(foreignkey.to_col):
            #         for refered in self.get_table(from_table).get_column_data(foreignkey.from_col):
            #             if record != refered:
            #                 mark = True
            #     if not mark:
            #         rows = self.insert(to_table, {})
            #         self.__process_unique_constraint(to_table)
        except Exception as e:
            logger.info(f'process foreign key Error : {e}')

    def reset(self):
        for t, tbl in self._tables.items():
            tbl.reset()

    def debug_print(self, table_name = None, indent = 2):        
        tbls = [tbl.debug_print() for t, tbl in self._tables.items() if table_name is None or t == table_name]
        s = {self.name: {'Table':tbls, 'Foreign Key': [fk.as_dict() for fk in self.foreign_keys], 'Context': self.context}}
        return s
    def to_ddl(self, dialect = 'sqlite'):
        ddls = []
        # self.post_process()
        for table in self.topo_tables():
            ddl = table.sql(dialect= dialect)
            ddls.append(ddl)
        return ddls

    def to_json(self, dialect = 'sqlite'):
        scm = []
        for tbl_name, tbl in self._tables.items():
            t = {
                'table_name': tbl_name,
                'columns': []
                }
            for column_def in tbl.column_defs:
                t['columns'].append([column_def.name, column_def.kind.sql(dialect= dialect), ''])
            scm.append({
                    'table_name' : tbl_name,
                    'table_description' :'',
                    'columns' : t['columns']
                })
        return scm
    def post_process(self):
        try:
            for from_tbl, foreignkeys in self.foreign_keys.items():
                for fk in foreignkeys:
                    from_column = fk.expressions[0].this
                    ref = fk.args.get('reference').this
                    to_tbl = ref.this.name
                    to_column = ref.expressions[0].name

                    ## Positive Branch
                    for record in self.get_table(from_tbl).get_column_data(from_column):
                        mark = any(record == ref_data for ref_data in self.get_table(to_tbl).get_column_data(to_column))
                        if not mark:
                            self.insert(to_tbl, concretes= {to_column: record.concrete})
                    # Negative Branch
                    neg_mark = False
                    for record in self.get_table(to_tbl).get_column_data(to_column):
                        if all(record != dep_data for dep_data in self.get_table(from_tbl).get_column_data(from_column)):
                            neg_mark = True
                    if not neg_mark:
                        self.insert(to_tbl, concretes= {})
        except Exception as e:
            logger.info(f'process foreign key Error : {e}')

    def to_insert(self, dialect = 'sqlite'):
        stmts = []
        self.post_process()
        for table in self.topo_tables():
            data2= []
            for row in table:
                data2.append(exp.Tuple(expressions = [exp.maybe_parse(r.to_db(), dialect =  dialect)  for r in row]))
            insert_query = exp.Insert(this = exp.Schema(this = exp.Table(this = f"`{str(table.name)}`"), expressions = [f"`{str(column_def.name)}`" for column_def in table.column_defs]), \
                                    expression = exp.Values(expressions = [*data2]))
            if data2: stmts.append(insert_query.sql(dialect= dialect))
        return stmts


    def to_instance(self, connection_string, save_ddl = True, dialect = 'sqlite'):
        stat = []
        conn = None
        db_name = self.name or 'default'
        conn = Connection(connection_string= connection_string)
        # self.post_process()

        for table in self.topo_tables():
            # logger.info(f'{table.name} --> {table.shape} --> {connection_string}')
            drop_exist_tbl = f"DROP TABLE IF EXISTS `{table.name}`"
            ddl = table.sql(dialect= dialect)
            print(f'ddl: {ddl}')
            stat.append(ddl)
            conn.execute_ddl(drop_exist_tbl)
            conn.execute_ddl(ddl)
            data2= []
            for row in table:
                data2.append(exp.Tuple(expressions = [exp.maybe_parse(r.to_db())  for r in row]))
            

            insert_query = exp.Insert(this = exp.Schema(this = exp.Table(this = f"`{str(table.name)}`"), expressions = [column_def.this for column_def in table.column_defs]), \
                                    expression = exp.Values(expressions = [*data2]))                
            
            if data2: 
                r = conn.execute_ddl(insert_query.sql(dialect= dialect))