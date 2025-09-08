
from __future__ import annotations
from dataclasses import dataclass, field, asdict, fields
import ast, re, z3, sqlglot, random, logging, time

from sqlglot import expressions as exp
import typing as t
from .table import Table, Row
from parseval import datatypes as dt
from parseval.symbol import Term, NULL
from .connection import Connection
from parseval.disjoint_set import DisjointSet

from collections import defaultdict

logger = logging.getLogger('app')

UNIQUE_RATIO = 0.8
DUPLICATE_RATIO = 0.1
NULL_RATIO = 0.1
    
def _generate_next2(data_generator, column_name: str, column_dtype, existing_data: t.List, notnull = False, unique = False):    
    if unique or len(existing_data) < 2:
        return {column_name :data_generator(column_name= column_name, dtype= column_dtype, unique = unique) }
    num_unique = len(set(existing_data))
    num_total = len(existing_data)
    num_nulls = len(list(filter(lambda x: NULL.is_null(x), existing_data)))
    num_duplicates = num_total - num_unique - num_nulls
    # Calculate current ratios
    current_null_ratio = num_nulls / num_total if num_total > 0 else 0
    current_unique_ratio = num_unique / num_total if num_total > 0 else 1
    current_duplicate_ratio = num_duplicates / num_total if num_total > 0 else 0

    deviations = {
        'unique' : current_unique_ratio - UNIQUE_RATIO,
        'duplicate' : current_duplicate_ratio - DUPLICATE_RATIO
    }
    if not notnull:
        deviations['null'] = current_null_ratio - NULL_RATIO
    weights = {key: - deviations[key] if deviations[key] < 0 else 0 for key in deviations}
    total_weight = sum(weights.values())
    if total_weight == 0:
        next_val_typ = random.choice(list(deviations.keys()))
    else:
        normalized_weights = {key: weight / total_weight for key, weight in weights.items()}
        next_val_typ = random.choices(list(normalized_weights.keys()), weights=normalized_weights.values())[0]
    
    if next_val_typ == 'unique':
        #  DefaultValueGenerator().next_value(dt.normalize(column.kind), column.name, column.kind.name, unique) 
        return {column_name : data_generator(column_name= column_name, dtype= column_dtype, unique = unique)}
    elif next_val_typ == 'duplicate':
        return {column_name : random.choice(existing_data)}
    else:
        return {column_name :  NULL(dt.normalize(column_dtype))}

class Instance:
    
    POSITIVE_BRANCH_COVER_FACTOR: int = 1
    NEGATIV_BRANCH_COVER_FACTOR: int = 1

    DEFAULT_GROUP_SIZE:int = 5
    DEFAULT_GROUP_COUNT:int = 3
    DUPLICATE_RATIO: float = 0.1

    NULL_RATIO: float = 0.1
    
    @classmethod
    def create(cls,  sql, name = 'public', dialect = None) -> Instance:
        paths = DisjointSet()
        context = ({}, paths, defaultdict(set), {})
        ctx = z3.Context()
        ddls = sqlglot.parse(sql, dialect = dialect)
        tables = {}
        foreign_keys = {}
        for ddl in ddls:
            tbl = Table.from_ddl(ddl=ddl)
            tables[tbl.name] = tbl
            foreign_keys[tbl.name] = tbl.foreign_keys
        return cls(context, name, tables, foreign_keys = foreign_keys, dialect = dialect, ctx = ctx)
    def __init__(self, context, name, tables: t.Dict[str, Table] | None = None, foreign_keys: t.Dict[str, t.List[exp.ForeignKey]]= None, dialect = None, ctx = None) -> None:
        self.context = context
        self.name = name
        self._tables: t.Dict[str, Table] = tables
        self.foreign_keys: t.Dict[str,t.List[exp.ForeignKey]] = foreign_keys if foreign_keys is not None else {}
        self.ctx = ctx if ctx is not None else z3.Context()
        self.dialect = dialect
        self.decls = self.context[0]
        self.path: DisjointSet = self.context[1]
        self.row_count = 1
        self._solver = Solver(context = self.context, ctx = self.ctx)
        self._solver.instance = self

        self.data_generator = DataGenerator()
    
    def set_state(self):
        self.states = {}
        for table_name, table in self._tables.items():
            self.states[table_name] = table.shape[0]

    def reset_state(self):
        for table_name, shape in self.states.items():
            table = self.get_table(table_name)
            while table.shape[0] > shape:
                row = table._data.pop()
                for term in row:
                    idt = str(term.symbol)
                    if idt in self.context[2]:
                        del self.context[2][idt]




    @staticmethod
    def new_solver(context, ctx, **kw) -> 'Solver':
        return Solver(context= context, ctx= ctx, **kw)
    
    @property
    def solver(self) -> 'Solver':
        return self._solver

    def estimate_row_count(self, val):
        self.row_count = max(self.row_count, val)

    def check_sat(self, constraints):
        paths = set()
        for constraint in constraints:
            paths.add(constraint)
        return self.solver.check(*paths)
    

    def preprocess(self):
        # unify data types
        for tbl, foreignkeys in self.foreign_keys.items():
            self.get_table()
            ...
        for foreignkey in self.foreign_keys:
            from_tbl = foreignkey.from_tbl
            from_col = foreignkey.from_col
            to_tbl = foreignkey.to_tbl
            to_col = foreignkey.to_col
            from_dtype = dt.normalize(self.get_table(from_tbl).get_column(from_col).dtype)
            to_dtype = dt.normalize(self.get_table(to_tbl).get_column(to_col).dtype)
            self.get_table(from_tbl).get_column(from_col).args['notnull'] = True
            if from_dtype != to_dtype:
                if 'Int' in [from_dtype, to_dtype]:
                    if from_dtype == 'Int':
                        self.get_table(to_tbl).get_column(to_col).args['dtype'] = self.get_table(from_tbl).get_column(from_col).dtype
                    if to_dtype == 'Int':
                        self.get_table(from_tbl).get_column(from_col).args['dtype'] = self.get_table(to_tbl).get_column(to_col).dtype

    def get_table(self, table_name) -> Table:
        return self._tables[table_name]
    
    def add_constraint(self, smt_expr: t.Union[Term, z3.ExprRef]):
        constraints = smt_expr.symbol if isinstance(smt_expr, Term) else smt_expr
        for constraint in ensure_cnf_constraints([constraints]):
            for vari in get_all_vars(constraint):
                self.path.add(vari)
                self.path.add(constraint)
                self.path.union(vari, constraint)

    def ensure_unique(self, table_name):
        table = self.get_table(table_name)
        pk_column_idx = [list(table._columns.keys()).index(identifier.name) for identifier in table.primary_key.expressions]
        mappings = {}
        seen = set()
        MAXTRIES = 1000
        
        for row in table:
            pk_data =tuple(row[idx] for idx in pk_column_idx)
            if not pk_data:
                break
            tries = 0
            while pk_data in seen and tries < MAXTRIES:
                tries += 1
                for cidx, term in enumerate(pk_data):
                    column_name = list(table._columns.keys())[pk_column_idx[cidx]]
                    existing_data = [d.concrete for d in table.get_column_data(column_name)]
                    if str(term.concrete) in mappings:
                        term.concrete = mappings[ str(term.concrete)]
                    else:
                        new_val = _generate_next2(self.data_generator, column_name, term.key, existing_data, notnull= True, unique = True)[column_name]
                        mappings[ str(term.concrete) ] = new_val
                        term.concrete = new_val
            seen.add(pk_data)
        return mappings
    def insert(self, table_name, concretes):
        table = self.get_table(table_name)
        row = []
        futures = []
        for column_name, column_def in table._columns.items():
            concrete = concretes.get(column_name, None)
            if concrete is None:
                existing_data = [v.concrete for v in table.get_column_data(column_name)]
                kind = table.get_column(column_name).kind.this
                futures.append(submit_work(_generate_next2, self.data_generator, column_name, kind.name, existing_data, table.is_notnull(column_name), table.is_unique(column_name)))
        for future in as_completed(futures):
            try:
                result = future.result(timeout= 15)
                concretes.update(result)
            except Exception as e:
                import traceback
                logger.error(traceback.format_exc())


        for column_name, column_def in table._columns.items():
            dtype = column_def.kind
            z_name = "%s_%s_%s_%s".replace(" ", '') % (table.name, column_name, dtype.name, table.shape[0])
            concrete = concretes.get(column_name, None)
            z_value = Term.create(context= self.context,  dtype= dtype, z_name= z_name, v= concrete, ctx= self.ctx)
            row.append(z_value)
        self.path.add(row[0].symbol)
        for r in row[1:]:
            self.path.add(r.symbol)
            self.path.union(row[0].symbol, r.symbol)

        r = Row(row)
        table.append(r)
        self.ensure_unique(table_name)
        return r

    def reset(self):
        for _, tbl in self._tables.items():
            tbl.reset()

    def debug_print(self, table_name = None, indent = 2):        
        tbls = [tbl.to_data() for t, tbl in self._tables.items() if table_name is None or t == table_name]
        s = {self.name: {'Table':tbls, 'Context': self.context}}
        return s
    

    def topo_tables(self) -> t.List[Table]:
        from collections import deque, defaultdict
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
                            row = self.insert(to_tbl, concretes= {to_column: record.concrete})
                    # Negative Branch
                    neg_mark = False
                    for record in self.get_table(to_tbl).get_column_data(to_column):
                        if all(record != dep_data for dep_data in self.get_table(from_tbl).get_column_data(from_column)):
                            neg_mark = True
                    if not neg_mark:
                        self.insert(to_tbl, concretes= {})
        except Exception as e:
            logger.info(f'process foreign key Error : {e}')

    def to_json(self):
        scm = []
        for tbl_name, tbl in self._tables.items():
            t = {
                'table_name': tbl_name,
                'columns': []
                }
            for column_name, column_def in tbl._columns.items():
                dtype = column_def.kind.sql()
                t['columns'].append([column_name, dtype, ''])
            scm.append({
                    'table_name' : tbl_name,
                    'table_description' :'',
                    'columns' : t['columns']
                })
        return scm

    def to_db(self, connection_string, save_ddl = True, dialect = 'sqlite'):
        stat = []
        conn = None
        
        db_name = self.name or 'default'
        conn = Connection(connection_string= connection_string)
        self.post_process()
        for table in self.topo_tables():
            logger.info(f'{table.name} --> {table.shape} --> {connection_string}')
            drop_exist_tbl = f"DROP TABLE IF EXISTS `{table.name}`"
            ddl = table.sql(dialect= dialect)
            stat.append(ddl)
            conn.execute_ddl(drop_exist_tbl)
            conn.execute_ddl(ddl)                
            # data = []
            data2= []
            for row in table:
                data2.append(exp.Tuple(expressions = [exp.maybe_parse(r.to_db())  for r in row]))
            insert_query = exp.Insert(this = exp.Schema(this = exp.Table(this = f"`{str(table.name)}`"), expressions = [column_def.this for _, column_def in table._columns.items()]), \
                                    expression = exp.Values(expressions = [*data2]))                
            
            if data2: 
                r = conn.execute_ddl(insert_query.sql(dialect= dialect))
  

    def to_ddl(self, dialect = 'sqlite') :
        ddls = []
        # self.post_process()
        for table in self.topo_tables():
            ddl = table.sql(dialect= dialect)
            ddls.append(ddl)
            # data2= []
            # for row in table:
            #     data2.append(exp.Tuple(expressions = [exp.maybe_parse(r.to_db(), dialect =  dialect)  for r in row]))
            
            # insert_query = exp.Insert(this = exp.Schema(this = exp.Table(this = f"`{str(table.name)}`"), expressions = [column_def.this for _, column_def in table._columns.items()]), \
            #                         expression = exp.Values(expressions = [*data2]))
            # if data2: ddls.append(insert_query.sql(dialect= dialect))
        return ddls
    
    def to_insert(self, dialect = 'sqlite'):
        stmts = []
        self.post_process()
        for table in self.topo_tables():
            data2= []
            for row in table:
                data2.append(exp.Tuple(expressions = [exp.maybe_parse(r.to_db(), dialect =  dialect)  for r in row]))
                # exp.parse_identifier(column_def.this, dialect= dialect).sql(dialect=dialect)
            insert_query = exp.Insert(this = exp.Schema(this = exp.Table(this = f"`{str(table.name)}`"), expressions = [f"`{str(column_def.name)}`" for _, column_def in table._columns.items()]), \
                                    expression = exp.Values(expressions = [*data2]))
            if data2: stmts.append(insert_query.sql(dialect= dialect))
        return stmts
