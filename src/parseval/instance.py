from __future__ import annotations
from typing import Dict, Any, List, Optional, Set
from sqlglot import parse, exp, MappingSchema
from sqlglot.schema import MappingSchema, flatten_schema, dict_depth, nested_get, nested_set, SchemaError

from collections import OrderedDict, defaultdict
from .helper import normalize_name
from src.parseval.plan.rex import Row, Symbol, Variable
from .states import raise_exception
from src.parseval.faker.domain import ColumnDomainPool
from src.parseval.db_manager import DBManager
from sqlglot.helper import name_sequence
import random, logging

logger = logging.getLogger("parseval.db")

class Catalog(MappingSchema):
    def __init__(self, schema = None, constraints = None, primary_keys = None, foreign_keys = None, visible = None, dialect = None, normalize = True):
        self.constraints = {}
        self.primary_keys = {}
        self.foreign_keys = {}
        schema = OrderedDict() if schema is None else schema
        super().__init__(schema, visible, dialect, normalize)
        constraints = {} if constraints is None else constraints
        primary_keys = {} if primary_keys is None else primary_keys
        foreign_keys = {} if foreign_keys is None else foreign_keys
        
        for table_name, table_constraints in constraints.items():
            for column_name, column_constraints in table_constraints.items():
                for constraint in column_constraints:
                    self.add_constraint(table_name, column_name, constraint)
        for table_name, pks in primary_keys.items():
            self.add_primary_key(table_name, pks)
        for table_name, fks in foreign_keys.items():
            self.add_foreign_key(table_name, fks)
            
    def _normalize(self, schema):
        normalized_mapping: Dict = OrderedDict()
        flattened_schema = flatten_schema(schema, depth=dict_depth(schema) - 1)
        for keys in flattened_schema:
            columns = nested_get(schema, *zip(keys, keys))
            if not isinstance(columns, dict):
                raise SchemaError(
                    f"Table {'.'.join(keys[:-1])} must match the schema's nesting level: {len(flattened_schema[0])}."
                )
            normalized_keys = [self._normalize_name(key, is_table=True, dialect= self.dialect, normalize= self.normalize) for key in keys]
            for column_name, column_type in columns.items():
                nested_set(
                    normalized_mapping,
                    normalized_keys + [self._normalize_name(column_name, dialect= self.dialect, normalize= self.normalize)],
                    column_type,
                )
        return normalized_mapping
    
    @property
    def tables(self):
        return self.mapping
    
    def add_primary_key(self, table: exp.Table | str, columns: List[exp.Identifier] | exp.Identifier):
        table = self._normalize_name(table if isinstance(table, str) else table.this, self.dialect, self.normalize)
        pk_set = self.primary_keys.setdefault(table, set())
        columns = [columns] if isinstance(columns, exp.Identifier) else columns
        pk_set.update(columns)
    def get_primary_key(self, table: exp.Table | str):
        table = self._normalize_name(table if isinstance(table, str) else table.this, self.dialect, self.normalize)
        return self.primary_keys.get(table, set())
    
    def add_foreign_key(self, table: exp.Table | str, foreign_key: List[exp.ForeignKey] | exp.ForeignKey):
        table = self._normalize_name(table if isinstance(table, str) else table.this, self.dialect, self.normalize)
        fk_list = self.foreign_keys.setdefault(table, [])
        fks = [foreign_key] if isinstance(foreign_key, exp.ForeignKey) else foreign_key
        fk_list.extend(fks)
    def get_foreign_key(self, table: exp.Table | str):
        table = self._normalize_name(table if isinstance(table, str) else table.this, self.dialect, self.normalize)
        return self.foreign_keys.get(table, [])
    
    def add_constraint(self, table: exp.Table | str, column: exp.Column | str, constraint: List[exp.ColumnConstraint] | exp.ColumnConstraint):
        table = self._normalize_name(table if isinstance(table, str) else table.this, self.dialect, self.normalize)
        column = self._normalize_name(column if isinstance(column, str) else column.this, normalize= self.normalize)
        table_constraints = self.constraints.setdefault(table, {})
        column_constraints = table_constraints.setdefault(column, set())
        constraints = [constraint] if isinstance(constraint, exp.ColumnConstraint) else constraint
        column_constraints.update(constraints)
    
    def get_column_constraints(self, table: exp.Table | str, column: exp.Column | str):
        table = self._normalize_name(table if isinstance(table, str) else table.this, self.dialect, self.normalize)
        column = self._normalize_name(column if isinstance(column, str) else column.this, normalize= self.normalize)
        table_constraints = self.constraints.get(table, {})
        column_constraints = table_constraints.get(column, set())
        return column_constraints
    
    def nullable(self, table: exp.Table | str, column: exp.Column| str,normalize: Optional[bool] = None):
        for constraint in self.get_column_constraints(table,column):
            if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
        for pk in self.get_primary_key(table):
            if pk.name == (column if isinstance(column, str) else column.this):
                return False
        return True    
    
    def is_unique(self, table: exp.Table | str, column: exp.Column| str,normalize: Optional[bool] = None):  
        for constraint in self.get_column_constraints(table,column):
            if isinstance(
                constraint.kind,
                (
                    exp.UniqueColumnConstraint,
                    exp.PrimaryKeyColumnConstraint,
                ),
            ):
                return True
        for pk in self.get_primary_key(table):
            if pk.name == (column if isinstance(column, str) else column.this):
                return True
            
        return False

class Instance(Catalog):
    def __init__(self, ddls: str, name:str, dialect: str, normalize=True, host_or_path: Optional[str] = None, database: Optional[str] = None, port: Optional[int] = None, username: Optional[str] = None, password: Optional[str] = None):
        super().__init__(dialect = dialect, normalize = normalize)
        self.ddls = ddls
        self.name = name
        # self.foreign_keys = defaultdict(lambda: defaultdict(list))
        self.column_domains = ColumnDomainPool()
        self._build_catalog2(ddls, dialect)
        # initialize column domain pool and register domain specs
        self.data: Dict[str, List[Row]] = defaultdict(list)  # table_name -> List[Row]
        
        self.symbols = {}
        self.symbol_to_table = {}
        self.symbol_to_tuple_id = {}
        self.tuple_id_to_symbols = {}
        self.pk_fk_symbols = {}
        
        self.host_or_path = host_or_path
        self.database = database or self.name
        self.port = port
        self.username = username
        self.password = password
        
        self.name_seq = name_sequence(self.name)
        
        
    def _build_catalog2(self, ddls: str, dialect: str):
        dependency, table_constraints = {}, {}
        def _build(ddl: exp.Create, maps: Dict, deps: Dict, pks: Dict, fks: Dict, tbl_constraints: Dict):
            table_name = ddl.this.this.name
            if table_name not in deps:
                deps[table_name] = 0
            table_mapping = maps.setdefault(table_name, {})
            constraints = tbl_constraints.setdefault(table_name, {})
            for node in ddl.dfs():
                if isinstance(node, exp.ColumnDef):
                    table_mapping[node.name] = node.kind.sql(dialect=dialect)
                    constraints.setdefault(node.name, set()).update(node.constraints)
                elif isinstance(node, exp.PrimaryKey):
                    pks.setdefault(table_name, set()).update(node.expressions)
                elif isinstance(node, exp.ForeignKey):
                    ref_table = node.args.get("reference").find(exp.Table).name
                    deps[ref_table] = deps.get(ref_table, 0) + 1
                    fks.setdefault(table_name, []).append(node)
        
        ddls = parse(ddls, dialect = dialect)
        mappings = {}
        primary_keys: Dict[str, Set[exp.Identifier]] = {}
        foreign_keys: Dict[str, List[exp.ForeignKey]] = {}
        logging.info(f"table, primary_keys before building catalog: {primary_keys}")
        for stmt_expr in ddls:
            _build(ddl=stmt_expr.this, maps=mappings, deps=dependency, pks= primary_keys, fks=foreign_keys, tbl_constraints=table_constraints)        
        sorted_table = OrderedDict(
            {
                tbl_name[0]: mappings[tbl_name[0]]
                for tbl_name in sorted(
                    dependency.items(), key=lambda item: item[1], reverse=True
                )
            }
        )
        for tbl_name, table_columns in sorted_table.items():
            self.add_table(tbl_name, table_columns, dialect= dialect)
            self.add_primary_key(tbl_name, primary_keys.get(tbl_name, set()))
            self.add_foreign_key(tbl_name, foreign_keys.get(tbl_name, []))
            for column in table_columns:
                if column in table_constraints.get(tbl_name, {}):
                    self.add_constraint(tbl_name, column, table_constraints.get(tbl_name).get(column, set()))               
        
        for table_name, columns in self.tables.items():
            logging.info(f"Registering domains for table {table_name} with #{len(columns)} columns")
            for column, datatype in columns.items():
                self.column_domains.register_domain(table= table_name, column= column, datatype= datatype, unique= self.is_unique(table_name, column), nullable= self.nullable(table_name, column))
        # Link pools for foreign keys so referenced and referencing columns share domain values
        # try:
        for table_name, fks in self.foreign_keys.items():
            for fk in fks:
                local_col = fk.expressions[0].this
                ref_table = fk.args.get("reference").find(exp.Table).name
                ref_col = fk.args.get("reference").this.expressions[0].this
                try:
                    da = f'{table_name}.{local_col}'
                    db = f'{ref_table}.{ref_col}'
                    self.column_domains.add_dependency(da, db)
                except Exception:
                    continue
            
    def __repr__(self):
        return f"Instance(name={self.name}, tables={list(self.tables.keys())})"

    def add_row(self, table_name: str, row: Row):
        table_name = self._normalize_table(table_name, dialect= self.dialect)
        self.data[table_name].append(row)
        
    def get_rows(self, table_name) -> List[Row]:
        table_name = self._normalize_table(table_name, dialect= self.dialect)
        return self.data[table_name]

    def get_row(self, table_name, index):
        return self.get_rows(table_name)[index]

    def get_column_data(self, table_name, column_name) -> List[Symbol]:
        column_name = self._normalize_name(column_name, dialect= self.dialect)
        return [row[column_name] for row in self.get_rows(table_name)]

    def create_rows(self, concretes: Dict[str, Dict[str, List[Any]]], sync_db: bool = False) -> Dict[str, List[Row]]:
        """
        Add multiple tuples to tables.

        Args:
            concretes: Map of table names to list of concrete values for new tuples of each column
            {
                table_name: {
                    column_name: [v1, v2, v3]
                }
            }

        Returns:
            Dict[str, List[Row]]: Map of table names to their new tuples
        """
        created_rows = {}
        
        for table_name in self.tables:
            if table_name not in concretes:
                continue
            table_data = concretes.get(table_name, {})
            # Normalize column names
            normalized_data = {}
            for col, vals in table_data.items():
                norm_col = self._normalize_name(col, dialect=self.dialect)
                normalized_data[norm_col] = vals
            num_rows = max(len(v) for v in normalized_data.values()) if normalized_data else 1
            created_rows[table_name] = []
            
            for ridx in range(num_rows):
                row_values = {}
                for col, col_values in normalized_data.items():
                    if ridx < len(col_values):
                        row_values[col] = col_values[ridx]
                    
                row = self.create_row(
                    table_name=table_name,
                    values=row_values,
                    sync_db=sync_db
                )
                created_rows[table_name].append(row)
            logger.info(f"Created row for table {table_name} with values {len(created_rows[table_name])}")
        return created_rows
    

    def create_row(
        self,
        table_name: str,
        values: Dict[str, List[Any]] | None = None,
        alias: Optional[str] = None,
        sync_db: bool = False,
    ) -> Dict[str, Any]:
        """
        Add a tuple to table and its dependent tables to maintain referential integrity.

        Args:
            table_name: Name of the table to expand
            values: Initial values for the new tuple

        Returns:
            Dict[str, Row]: Map of table names to their new tuples
        """
        table_name = self._normalize_name(table_name, dialect= self.dialect)
        values = values or {}
        new_tuples = defaultdict(list)
        positions: Dict[str, int] = {}
        fk_infos = self.get_foreign_key(table_name)
        referenced_tables = set()
        # Find missing foreign key values        
        for fk in fk_infos:
            local_col = fk.expressions[0].name
            ref_table = fk.args.get("reference").find(exp.Table).name
            ref_col = fk.args.get("reference").this.expressions[0].name
            if local_col not in values:
                referenced_tables.add((ref_table, ref_col, local_col))
        for ref_table_name, ref_col_name, local_col_name in referenced_tables:
            existing_values = self.get_column_data(ref_table_name, ref_col_name)
            used_values = set(
                d.concrete for d in self.get_column_data(table_name, local_col_name)
            )
            available_values = [
                (idx, val.concrete)
                for idx, val in enumerate(existing_values)
                if not (self.is_unique(table_name, local_col_name) and val.concrete in used_values)                
            ]
            if available_values:
                idx, chosen_value = random.choice(available_values)
                values[self._normalize_name(local_col_name)] = chosen_value
            else:
                ref_values = {}
                # materialize referenced row so FK points to an actual tuple
                ref_position = self._create_row(ref_table_name, ref_values, alias=None, sync_db=sync_db)
                ref_value = self.get_column_data(ref_table_name, ref_col_name)[
                    ref_position
                ]
                values[self._normalize_name(local_col_name)] = ref_value.concrete
                new_tuples[self._normalize_name(ref_table_name)].append(
                    self.get_row(ref_table_name, ref_position)
                )
        # Step 2: Create the main row
        main_pos = self._create_row(table_name, values, alias=alias)
        new_tuples[table_name].append(self.get_row(table_name, main_pos))
        positions[table_name] = main_pos
        return {"rows": new_tuples, "positions": positions}

    def _create_row(
        self,
        table_name: str,
        concretes: Dict[str, Any],
        alias: Optional[str] = None,
        sync_db: bool = False,
    ):
        """
        Internal helper method to create a row in a table with associated symbols.

        Args:
            table_name: Name of the table
            concretes: Concrete values for column in the table

        Returns:
            int: Index of the new row
        """
        
        table_expr = self.tables[table_name]
        tuple_index = len(self.get_rows(table_name))
        for _ in range(100):
            new_values = {}
            for column, datatype in table_expr.items():
                z_name = normalize_name(
                    "%s_%s_%s_%s"
                    % (table_name, column, str(datatype), tuple_index)
                )
                if column in concretes:
                    concrete = concretes.get(column)
                else:
                    # Use ColumnDomainPool's ValuePool when possible to sample/generate
                    pool = self.column_domains.get_or_create_pool(table_name, column)
                    concrete = pool.generate()
                    pool.add_generated_value(concrete)
                    
                z_value = Variable(this = z_name, _type=datatype, concrete=concrete)
                z_value.type = datatype
                new_values[column] = z_value
                # new_values.append(z_value)
                self.symbols[z_name] = z_value
                self.symbol_to_table[z_name] = (table_name, column)
            rowid = "%s_rowid_%d" % (table_name, tuple_index)
            # row = Row(rowid, (new_values ))
            row = Row(this = rowid, columns = new_values)
            if sync_db:
                try:
                    self.sync_db(table_name, row)
                    self.add_row(table_name, Row(rowid, (new_values)))
                    # self.data[table_name].append(Row(rowid, (new_values)))
                    return tuple_index
                except Exception as e:
                    continue
            else:
                self.add_row(table_name, Row(this = rowid, columns = new_values))
                
                return tuple_index
        raise_exception(f"Failed to create row for table {table_name} after 100 attempts")

    def reset(self):
        """Clear instance data and reinitialize column domain pools.
        Preserves `catalog` and `foreign_keys` (schema), but clears generated
        rows, symbols and recreates `ColumnDomainPool` and registered domains.
        """
        self.data.clear()
        self.symbols.clear()
        self.symbol_to_table.clear()
        self.symbol_to_tuple_id.clear()
        self.tuple_id_to_symbols.clear()
        self.pk_fk_symbols.clear()
        # recreate pool and reregister domains
        self.column_domains = ColumnDomainPool()
        self._build_catalog2(self.ddls, self.dialect)


    def sync_db(self, table, row):
        database = self.database
        if self.dialect == "sqlite":
            database = database if database.endswith(".sqlite") else database + ".sqlite"
        with DBManager().get_connection(self.host_or_path, database, port= self.port, username= self.username, password= self.password) as conn:
            
            columns = []
            parameters = []
            for column_name in self.column_names(table):
                columns.append(f'"{column_name}"')
                parameters.append(f":{normalize_name(column_name)}")
            mapped_data = []
            data = {}
            for column_name, column_value in zip(columns, row.columns):
                data[normalize_name(column_name)] = column_value.concrete
            mapped_data.append(data)
            if mapped_data:
                column_list = ", ".join(columns)
                stmt = f"INSERT INTO {table} ({column_list}) VALUES ({', '.join(parameters)})"
                conn.insert(stmt, mapped_data)
            
    def to_db2(self, host_or_path, database=None, port=None, username=None, password=None):
        database = database or self.name
        if self.dialect == "sqlite":
            database = database if database.endswith(".sqlite") else database + ".sqlite"
        with DBManager().get_connection(host_or_path, database, port= port, username= username, password= password) as conn:            
            conn.create_schema(self.ddls, dialect = self.dialect)
            all_rows = conn.get_all_table_rows()
            concretes = {}
            for table in all_rows:
                if not all_rows[table]:
                    continue
                concretes[table] = []
                columns = all_rows[table][0]
                for row in all_rows[table][1:]:
                    values = {name: value for name, value in zip(columns, row) }
                    concretes[table].append(values)
            for table_name in self.tables:
                for row in concretes.get(table_name, []):
                    self.create_row(
                        table_name=table_name, values= row
                    )
                
    def to_db(
        self, host_or_path, database=None, port=None, username=None, password=None
    ):
        database = database or self.name
        database = database if database.endswith(".sqlite") else database + ".sqlite"

        mapped_data = []

        with DBManager().get_connection(
            host_or_path=host_or_path,
            database=database,
            port=port,
            username=username,
            password=password,
            dialect=self.dialect,
        ) as conn:
            conn.create_tables(*self.ddls.split(";"))

            for table_name in self.tables:
                rows = self.get_rows(table_name)
                columns = []

                parameters = []
                for column_name in self.column_names(table_name):
                # for column in self.(table_name).columns:
                    columns.append(f'"{column_name}"')
                    parameters.append(f":{normalize_name(column_name)}")
                mapped_data = []
                for row in rows:
                    
                    data = {
                        normalize_name(column_name): column.concrete for column_name, column in row.items()
                    }
                    
                    # for column_name, column_value in row.items():
                    #     data[normalize_name(column_name)] = column_value.concrete
                    mapped_data.append(data)
                if mapped_data:

                    column_list = ", ".join(columns)
                    stmt = f"INSERT INTO {table_name} ({column_list}) VALUES ({', '.join(parameters)})"
                    with open(f"examples/db/{self.name}_data_inserts.sql", "a") as f:
                        f.write(f"-- Inserting into table: {table_name} --\n")
                        for data in mapped_data:
                            cols = ", ".join(data.keys())
                            vals = ", ".join(
                                [
                                    f"'{str(v)}'" if isinstance(v, str) else str(v)
                                    for v in data.values()
                                ]
                            )
                            f.write(
                                f"INSERT INTO {table_name} ({cols}) VALUES ({vals});\n"
                            )
                    conn.insert(stmt, mapped_data)

        return database


from parseval.helper import compare_df
def early_stopper(instance: Instance, gold: str, pred: Optional[str] = None) -> bool:
    dbname = instance.name_seq()
    try:
        instance.to_db(instance.host_or_path, dbname, port= instance.port, username= instance.username, password= instance.password)
    except Exception as e:
        logger.error(f'Error when generating concrete database: {e}')
        return True
    
    with DBManager().get_connection(instance.host_or_path, dbname, instance.username, instance.password, instance.port, instance.dialect) as conn:
        gold_ret = conn.execute(gold, fetch= "all")
        if pred is not None:
            pred_ret = conn.execute(pred, fetch= "all")
            if not compare_df(gold_ret, pred_ret):
                return True
            return False
        return True if len(gold_ret) > 3 else False
            
            
            
            
        
                
                
        
            
    
    
    
    