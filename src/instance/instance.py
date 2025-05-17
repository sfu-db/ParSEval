
from __future__ import annotations
import sqlglot.generator
import ast, re, sqlglot, random, logging
from sqlglot import  exp
from typing import List, Dict, Any, Optional, Union, Set, Sequence, TypeVar, Generic, Tuple, Generator

from collections import defaultdict, OrderedDict
from src.corekit import DBManager

from .generators import ValueGeneratorRegistry

from src.expression.symbol import to_variable, Expr, distinct, and_, or_

from .helper import clean_name, convert, random_value_from_list

from .table import Table, Row

logger = logging.getLogger('src.parseval.instance')


class Instance:
    @classmethod
    def create(cls, schema: str, name = 'pulic', dialect: str = 'sqlite'):
        ddls = sqlglot.parse(schema, dialect = dialect)
        deps, tables, foreign_keys = {}, OrderedDict(), {}       
        for stmt_expr in ddls:
            tbl = Table.create(stmt_expr)
            tables[tbl.name] = tbl
            foreign_keys[tbl.name] = tbl.foreign_keys
            if tbl.name not in deps: deps[tbl.name] = 0
            for fk in tbl.foreign_keys:
                from_table = fk.args.get('reference').find(exp.Table).name
                deps[from_table] = deps.get(from_table, 0) + 1
        sorted_table = OrderedDict({tbl_name[0]: tables[tbl_name[0]] \
                                    for tbl_name in sorted(deps.items(), key=lambda item: item[1], reverse=True)})
        
        return cls(name, tables = sorted_table, foreign_keys = foreign_keys, dialect = dialect)
            

    def __init__(self, name, tables: Dict[str, Table] | None = None, **kw) -> None:        
        self.name = name
        self.foreign_keys: Dict[str, List[exp.ForeignKey]] = kw.get('foreign_keys', {})
        self._tables: Dict[str, Table] = tables
        self.dialect = kw.pop('dialect', 'sqlite')

        self._foreign_key_cache = {}
        for table_name, foreign_keys in self.foreign_keys.items():
            self._foreign_key_cache[table_name] = {}
            for fk in foreign_keys:
                local_col = fk.expressions[0].this
                ref_table = fk.args.get('reference').find(exp.Table).name
                ref_col = fk.args.get('reference').this.expressions[0].this
                self._foreign_key_cache[table_name][local_col] = (ref_table, ref_col) 
        self.init_context()

    def init_context(self):
        self.symbols = {}
        self.symbol_to_table = {}
        self.symbol_to_tuple_id = {}
        self.tuple_id_to_symbols = {}
        self.pk_fk_symbols = {}

    def get_table(self, table_name: str) -> Table:
        """
        Get a table by name.
        
        Args:
            table_name: Name of the table
            
        Returns:
            Table: The table object
            
        Raises:
            TableNotFoundError: If the table does not exist
        """
        return self._tables[table_name]
        

    def create_row(self, table_name: str, values: Dict[str, Any]) -> Dict[str, Row]:
        """
        Add a tuple to table and its dependent tables to maintain referential integrity.
        
        Args:
            table_name: Name of the table to expand
            values: Initial values for the new tuple
            
        Returns:
            Dict[str, rex.Row]: Map of table names to their new tuples
        """
        new_tuples = defaultdict(list)
        table = self.get_table(table_name)

        fk_info = self._foreign_key_cache.get(table_name, {})
        referenced_tables = set()
        # Find missing foreign key values
        for local_col, (ref_table, ref_col) in fk_info.items():
            if local_col not in values:
                referenced_tables.add((ref_table, ref_col, local_col))

        # Process referenced tables
        for ref_table, ref_col, local_col in referenced_tables:
            ref_table_obj = self.get_table(ref_table)
            ref_col_idx = ref_table_obj.get_column_index(ref_col)
            local_col_idx = table.get_column_index(local_col)

            existing_values = ref_table_obj.get_column_data(ref_col)
            used_values = set(d.value for d in table.get_column_data(local_col))

            available_values = [
                (idx, val.value) for idx, val in enumerate(existing_values)
                if not (table.is_unique(local_col) and val.value in used_values)
            ]
            if available_values:
                idx, chosen_value = random.choice(available_values)
                values[local_col] = chosen_value
            else:
                ref_values = {}
                ref_pos = self._create_row_internal(ref_table, ref_values)
                ref_value = ref_table_obj[ref_pos][ref_col_idx]
                values[local_col] = ref_value.value
                new_tuples[ref_table].append(ref_table_obj[ref_pos])

        # Step 2: Create the main row
        main_pos = self._create_row_internal(table_name, values)
        new_tuples[table_name].append(table[main_pos])
        
        return new_tuples


    def _generate_concrete_for_column(self, table_name, column_name, column_type: str, is_unique: bool = False):
        """
        Generate a value for a column using the appropriate generator.
        
        Args:
            table_name: Name of the table
            column_name: Name of the column
            column_type: Type of the column (string or DataType)
            is_unique: Whether the value should be unique
            
        Returns:
            Any: A generated value for the column
        """
        existing_values = None
        if is_unique:
            table = self.get_table(table_name)
            existing_values = set(d.value for d in table.get_column_data(column_name))

        generator = ValueGeneratorRegistry.get_generator(column_type)
        return generator(is_unique=is_unique, existing_values=existing_values)


    def _create_row_internal(self, table_name: str, values: Dict, multiplicity: int = 1)                :
        """
        Internal helper method to create a row in a table with associated symbols.
        
        Args:
            table_name: Name of the table
            values: Values for the row
            multiplicity: Multiplicity of the row
            
        Returns:
            int: Index of the new row
        """
        table = self.get_table(table_name)
        tuple_index = table.shape[0]
        tuple_name = clean_name(f'R_{table_name}_t{tuple_index}')
        relation = to_variable('int', tuple_name, multiplicity)
        self.symbols[tuple_name] = relation
        
        new_values = []
        for column_index, column_def in enumerate(table.column_defs):
            column_dtype = column_def.kind.this.name
            z_name = clean_name("%s_%s_%s_%s" % (table_name, column_def.name, column_dtype, tuple_index))
            concrete = values.get(column_def.name, None)
            if concrete is None:
                concrete = self._generate_concrete_for_column(table_name, column_def.name, column_dtype, table.is_unique(column_def))
            z_value = to_variable(column_dtype, z_name, concrete)
            new_values.append(z_value)
            self.symbols[z_name] = z_value
            self.symbol_to_table[z_name] = (table_name, column_def.name, column_index)
            self.symbol_to_tuple_id[z_name] = relation
            self.tuple_id_to_symbols[tuple_name] = z_value
            if table.is_unique(column_def) or table.is_foreignkey(column_def):
                self.pk_fk_symbols[z_name] = z_value
        table.tuples.append(Row(operands = new_values, this = relation))
        return tuple_index


    def update_values(self, values: Dict[str, Any]):
        for k, v in values.items():
            self.symbols[k].set('value', v)
            

    def commit(self):
        for _, table in self._tables.items():
            for row in table.tuples[:]:
                if row.multiplicity.value == 0:
                    table.tuples.remove(row)
                    continue

    def _get_primary_key_constraints(self) -> List[Expr]:
        pk_constraints = []
        for table_name, table in self._tables.items():
            for pk_expr in table.primary_key.expressions:
                data = table.get_column_data(pk_expr.name)
                if data:
                    pk_constraints.append(distinct(data))
        return pk_constraints

    def _get_foreign_key_constraints(self) -> List[Expr]:
        fk_constraints = []

        for from_table_name, fk_info in self._foreign_key_cache.items():
            for local_col, (ref_table, ref_col) in fk_info.items():
                from_table = self.get_table(from_table_name)
                to_table = self.get_table(ref_table)
                smt_exprs = []
                for ref_row in to_table:
                    to_data = ref_row[to_table.get_column_index(ref_col)]
                    exprs = []
                    for from_row in from_table:
                        from_data = from_row[from_table.get_column_index(local_col)]
                        cond1 = to_data == from_data
                        cond2 = ref_row.multiplicity >= from_row.multiplicity
                        exprs.append(cond1.and_(cond2))
                    smt_exprs.append(or_(exprs))
                fk_constraints.append(and_(smt_exprs))
        return fk_constraints
    def _get_size_constraints(self) -> List[Expr]:
        size_constraints = []
        for from_table_name, fk_info in self._foreign_key_cache.items():
            for local_col, (ref_table, ref_col) in fk_info.items():
                from_table = self.get_table(from_table_name)
                to_table = self.get_table(ref_table)
                to_table_size = [to_row.multiplicity for to_row in to_table]
                from_table_size = [from_row.multiplicity for from_row in from_table]
                size_constraints.append(sum(to_table_size) <= sum(from_table_size))
        return size_constraints

    def get_db_constraints(self)-> Dict[str, List[Expr]]:
        pk_constraints = self._get_primary_key_constraints()
        fk_constraints = self._get_foreign_key_constraints()
        size_constraints = self._get_size_constraints()
        ## unique constraints
        ## range constraints
        return {'SIZE': size_constraints, 'PK': pk_constraints, 'FK': fk_constraints}

    def to_ddl(self) -> List[str]:
        stmts = []
        for table_name, table in self._tables.items():
            ddl = table.stmt
            # column_defs = [c for c in table.column_defs]
            # if table.primary_key and table.primary_key.expressions:
            #     column_defs.append(table.primary_key)
            # column_defs.extend(table.foreign_keys)
            # ddl = exp.Create(this = exp.Schema(this = exp.Table(this = exp.to_identifier(table_name, quoted= True)), expressions = column_defs), exists = True, kind = 'TABLE')
            stmts.append(ddl.sql(dialect= self.dialect))
        return stmts
    
    def _get_reference_table_column_names(self, table_name, column_name):
        if table_name in self._foreign_key_cache and column_name in self._foreign_key_cache[table_name]:
            return self._foreign_key_cache[table_name][column_name]
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
                    # if column_value.value is None:
                    #     logging.info(f'concrete: {concrete} <---> {column_value}')
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
                            concrete = self._generate_concrete_for_column(table_name, column_def.name, column_def.kind.this.name, table.is_unique(column_def))
                            concrete = convert(concrete)
                        if table.is_foreignkey(column_def):
                            from_table, from_column = self._get_reference_table_column_names(table_name, column_def.name)
                            ref_table_values = data[from_table][from_column]
                            if table.is_unique(column_def):
                                existing_values =  data[table_name][column_def.name]
                                concrete = random_value_from_list(ref_table_values, skips = existing_values,  default = None)
                                print(table.name, '===' * 30)
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
    def to_db(self, host_or_path, database = None, port = None, username = None, password = None):
        database = self.name if database is None else database
        with DBManager().get_connection(host_or_path= host_or_path, database= database,
                                        port = port, username= username, password= password, dialect= self.dialect) as conn:
            conn.create_tables(*self.to_ddl())
            inserts = self.to_insert()
            for insert_stmt, data in inserts:
                conn.insert(insert_stmt, data)