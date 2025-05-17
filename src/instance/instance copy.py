
from __future__ import annotations
import sqlglot.generator
import ast, re, z3, sqlglot, random, logging
from sqlglot import expressions as exp
from typing import List, Dict, Any, Optional, Union, Set, Sequence, TypeVar, Generic, Tuple, Generator
from src.uexpr import rex
from collections import defaultdict, OrderedDict
from src.corekit import DBManager
from src.symbols import create_symbol
from .helper import clean_name, generate_unique_value, convert, random_value_from_list
from .dataframe import DataFrame

logger = logging.getLogger('src.parseval.instance')


def create_instance(context, 
                    schema: str, 
                    initial_values: Dict[str, List[Dict[str, Any]]], 
                    name = 'pulic', 
                    size = 5, dialect = 'sqlite'):
        ddls = sqlglot.parse(schema, dialect = dialect)
        deps, tables, foreign_keys = {}, OrderedDict(), {}
        for stmt_expr in ddls:
            tbl = DataFrame.create(stmt_expr)
            tables[tbl.name] = tbl
            foreign_keys[tbl.name] = tbl.foreign_keys
            if tbl.name not in deps: deps[tbl.name] = 0
            for fk in tbl.foreign_keys:
                from_table = str(fk.args.get('reference').find(exp.Table))
                deps[from_table] = deps.get(from_table, 0) + 1
   
        sorted_table = OrderedDict({tbl_name[0]: tables[tbl_name[0]] \
                                    for tbl_name in sorted(deps.items(), key=lambda item: item[1], reverse=True)})
        instance =  Instance(context, name, tables = sorted_table, foreign_keys = foreign_keys, dialect = dialect)
        if not initial_values and size == 0:
            return instance
        for table_name in instance._tables:
            concretes = initial_values.get(table_name, [])
            row_size = max(size, len(concretes))
            for index in range(row_size):
                initials = concretes[index] if index < len(concretes) else {}
                tupp = instance.add_tuple(table_name, initials)
                
        return instance


class Instance:
    def __init__(self, context, name, tables: Dict[str, DataFrame] | None = None, **kw) -> None:
        self.context = context
        self.name = name
        self.foreign_keys: Dict[str, List[exp.ForeignKey]] = kw.get('foreign_keys', {})
        self._tables: Dict[str, DataFrame] = tables
        self.dialect = kw.pop('dialect', 'sqlite')

    def get_table(self, table_name) -> DataFrame:
        return self._tables[table_name]

    def commit(self):
        for table_name, table in self._tables.items():
            for row in table.tuples[:]:
                if row.multiplicity.value == 0:
                    table.tuples.remove(row)
                    continue
    
    def add_tuple(self, table_name: str, values: Dict) -> Dict[str, rex.Row]:
        '''
            Add a tuple to table and its dependent tables to maintain referential integrity.            
            Args:
                table_name: Name of the table to expand
                values: Initial values for the new tuple                
            Returns:
                Dict[str, int]: Map of table names to their new tuple
        '''
        new_tuples = defaultdict(list)
        referenced_tables = set()
        table = self.get_table(table_name)
        for foreign_key in table.foreign_keys:
            ref_table = str(foreign_key.args.get('reference').find(exp.Table))
            ref_column = str(foreign_key.args.get('reference').this.expressions[0].this)
            local_column = str(foreign_key.expressions[0].this)
            if local_column not in values:
                referenced_tables.add((ref_table, ref_column, local_column))
        for ref_table, ref_column, local_column in referenced_tables:
            ref_values = {}
            ref_table_obj = self.get_table(ref_table)
            existing_values = ref_table_obj.get_column_data(ref_column)
            need_new_tuple = True
            if existing_values:
                available_values = []
                used_values = [d.value for d in table.get_column_data(local_column)]
                for idx, val in enumerate(existing_values):
                    can_use = True
                    if table.is_unique(local_column) and val.value in used_values:
                        can_use = False
                    if can_use:
                        available_values.append((idx, val.value))
                if available_values:
                    need_new_tuple = False
                    idx, chosen_value = random.choice(available_values)
                    values[local_column] = chosen_value
            if need_new_tuple:
                ref_pos = self._add_single_tuple(ref_table, ref_values)
                new_tuples[ref_table].append(ref_table_obj[ref_pos])
                ref_value = ref_table_obj[ref_pos][ref_table_obj.get_column_index(ref_column)]
                values[local_column] = ref_value.value
        main_pos = self._add_single_tuple(table_name, values, multiplicity=1)
        new_tuples[table_name].append(table[main_pos])
        return new_tuples

    def _add_single_tuple(self, table_name: str,  values, multiplicity = 1) -> int:
        '''
            Helper method to add a single tuple to a table
        '''
        table = self.get_table(table_name)
        tuple_index = table.shape[0]
        tuple_name = clean_name(f'R_{table_name}_t{tuple_index}')
        relation = create_symbol('int', self.context, tuple_name, multiplicity)
        new_values = []
        for column_index, column_def in enumerate(table.column_defs):
            column_dtype = column_def.kind.this.name
            z_name = clean_name("%s_%s_%s_%s" % (table_name, column_def.name, column_dtype, tuple_index))
            concrete = values.get(column_def.name, None)
            if table.is_unique(column_def) and concrete is None:
                existing_values = [d.value for d in table.get_column_data(column_def.name)]
                concrete = generate_unique_value(table_name, column_def.name, column_def.kind, existing_values)
            z_value = create_symbol(column_dtype, self.context, z_name, concrete)
            new_values.append(z_value)
            self.context.set('symbol_to_table', {str(z_value.expr): (table_name, column_def.name, column_index)})
            self.context.set('symbol_to_tuple_id', {str(z_value.expr): relation})
            self.context.set('tuple_id_to_symbols', {str(relation.expr): z_value})
            if table.is_unique(column_def) or table.is_foreignkey(column_def):
                self.context.set('pk_fk_symbols', z_value.expr)
        table.tuples.append(rex.Row(expressions = new_values, multiplicity = relation))
        return tuple_index

    def _get_primary_key_constraints(self) -> List[z3.Expr]:
        pk_constraints = []
        for table_name, table in self._tables.items():
            for pk_expr in table.primary_key.expressions:
                pk_vals = [v.expr for v in table.get_column_data(pk_expr.name)]
                pk_constraints.append(z3.Distinct(*pk_vals))
        return pk_constraints

    def _get_foreign_key_constraints(self) -> List[z3.Expr]:
        fk_constraints = []
        for to_table_name, foreign_keys in self.foreign_keys.items():
            for foreign_key in foreign_keys:
                to_column_name = str(foreign_key.expressions[0].this)
                from_table_name = str(foreign_key.args.get('reference').find(exp.Table))
                from_column_name = str(foreign_key.args.get('reference').this.expressions[0].this)
                from_table = self.get_table(from_table_name)
                to_table = self.get_table(to_table_name)
                smt_exprs = []
                for to_row in to_table:
                    to_data = to_row[to_table.get_column_index(to_column_name)]
                    exprs = []
                    for from_row in from_table:
                        from_data = from_row[from_table.get_column_index(from_column_name)]
                        c1 = to_data == from_data
                        c2 = to_row.multiplicity <= from_row.multiplicity
                        exprs.append(z3.And(c1.expr, c2.expr))
                    smt_exprs.append(z3.Or(exprs))
                smt = z3.And(smt_exprs)
                fk_constraints.append(smt)
        return fk_constraints
        
    def _get_size_constraints(self) -> List[z3.Expr]:
        size_constraints = []
        for to_table_name, foreign_keys in self.foreign_keys.items():
            for foreign_key in foreign_keys:
                from_table_name = str(foreign_key.args.get('reference').find(exp.Table))
                from_table = self.get_table(from_table_name)
                to_table = self.get_table(to_table_name)
                to_table_size = [to_row.multiplicity.expr for to_row in to_table]
                from_table_size = [from_row.multiplicity.expr for from_row in from_table]
                size_constraints.append(sum(to_table_size) <= sum(from_table_size))
        return size_constraints

    def get_db_constraints(self)-> Dict[str, List[z3.Expr]]:
        pk_constraints = self._get_primary_key_constraints()
        fk_constraints = self._get_foreign_key_constraints()
        size_constraints = self._get_size_constraints()
        ## unique constraints
        ## range constraints
        return {'SIZE': size_constraints, 'PK': pk_constraints, 'FK': fk_constraints}


    def to_ddl(self) -> List[str]:
        stmts = []
        for table_name, table in self._tables.items():
            column_defs = [c for c in table.column_defs]
            if table.primary_key and table.primary_key.expressions:
                column_defs.append(table.primary_key)
            column_defs.extend(table.foreign_keys)
            ddl = exp.Create(this = exp.Schema(this = exp.Table(this = exp.to_identifier(table_name, quoted= True)), expressions = column_defs), exists = True, kind = 'TABLE')
            stmts.append(ddl.sql(dialect= self.dialect))
        return stmts
    
    def _get_reference_table_column_names(self, table_name, column_name):

        for foreign_key in self.foreign_keys.get(table_name, []):
            if column_name == str(foreign_key.expressions[0].this):
                from_table = str(foreign_key.args.get('reference').find(exp.Table))
                from_column = str(foreign_key.args.get('reference').this.expressions[0].this)
                return from_table, from_column
        return None, None
    def to_insert(self) -> List[str]:
        self.commit()
        stmts = []
        data = defaultdict(lambda : defaultdict(list))

        for table_name, table in self._tables.items():
            table_identifier = exp.to_identifier(table_name, quoted= True)
            columns = [exp.column(c.name) for c in table.column_defs]
            values = []
            for row in table:
                tup = []
                if row.multiplicity.value < 1:
                    continue
                for column_def, column_value in zip(table.column_defs, row):
                    concrete = convert(column_value)
                    data[table_name][column_def.name].append(concrete)
                    tup.append(concrete)
                values.append(exp.tuple_(*tup))

            for row in table:
                for _ in range(1, row.multiplicity.value):
                    tup = []
                    for column_def, column_value in zip(table.column_defs, row):
                        concrete = convert(column_value)
                        if table.is_unique(column_def):
                            existing_values = [v.sql() for v in data[table_name][column_def.name]]
                            concrete = generate_unique_value(table_name= table_name, 
                                                            column_name= column_def.name,
                                                            dtype= column_def.kind,
                                                            existing_values= set(existing_values))
                            concrete = convert(concrete)

                        if table.is_foreignkey(column_def):
                            from_table, from_column = self._get_reference_table_column_names(table_name, column_def.name)
                            ref_table_values = data[from_table][from_column]
                            if table.is_unique(column_def):
                                existing_values =  data[table_name][column_def.name]
                                concrete = random_value_from_list(ref_table_values, skips= existing_values,  default = None)
                            elif concrete not in ref_table_values:
                                concrete = random_value_from_list(ref_table_values, skips= [],  default = concrete)
                        data[table_name][column_def.name].append(concrete)
                        tup.append(concrete)
                    if tup:
                        values.append(exp.tuple_(*tup))
            if values:
                stmt = exp.Insert(this = exp.Schema(this = exp.Table(this = table_identifier), expressions = columns), expression = exp.Values(expressions = values))
                stmts.append((stmt.sql(dialect= self.dialect), None))
        return stmts
    def to_db(self, host_or_path, database, port = None, username = None, password = None):
        with DBManager().get_connection(host_or_path= host_or_path, database= database,
                                        port = port, username= username, password= password, dialect= self.dialect) as conn:
            conn.create_tables(*self.to_ddl())
            inserts = self.to_insert()
            for insert_stmt, data in inserts:
                conn.insert(insert_stmt, data)