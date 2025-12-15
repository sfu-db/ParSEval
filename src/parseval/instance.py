from __future__ import annotations
from src.parseval.plan.rex import Table, Catalog, Schema, ColumnRef
from typing import Dict, Any, List, Optional
from sqlglot import parse, exp
from collections import OrderedDict, defaultdict
from .helper import normalize_name
from .symbol import *
from src.parseval.smt.domain import ColumnDomainPool, DomainSpec
from src.parseval.db_manager import DBManager
import random, logging


class Instance:
    def __init__(
        self, ddls: str, name: str = "default", dialect: str = "sqlite"
    ) -> None:
        self.name = name
        self.ddls = ddls
        self.dialect = dialect
        self.foreign_keys = defaultdict(lambda: defaultdict(list))
        self._build_catalog(ddls, dialect)
        # initialize column domain pool and register domain specs
        self.column_domain = ColumnDomainPool()
        self._register_domains()

        self.data: Dict[str, List[Row]] = defaultdict(list)  # table_name -> List[Row]
        self.symbols = {}
        self.symbol_to_table = {}
        self.symbol_to_tuple_id = {}
        self.tuple_id_to_symbols = {}
        self.pk_fk_symbols = {}

    def _build_catalog(self, ddls, dialect):
        ddls = parse(ddls, dialect=dialect)
        dependency, tables = {}, OrderedDict()
        for stmt_expr in ddls:
            schema_expr = stmt_expr.this
            table_name = schema_expr.this.name
            column_defs, primary_key, foreign_key, constraints = [], None, None, {}

            for expr in schema_expr.expressions:
                if isinstance(expr, exp.ColumnDef):
                    constraints.setdefault(expr.this.name, set()).update(
                        expr.constraints
                    )
                    column_defs.append(
                        ColumnRef(
                            this=exp.to_identifier(expr.name),
                            datatype=DataType.build(dtype=str(expr.args.get("kind"))),
                            ref=len(column_defs),
                            table=table_name,
                        )
                    )

                elif isinstance(expr, exp.PrimaryKey):
                    primary_key = expr

                elif isinstance(expr, exp.ForeignKey):
                    local_column = expr.expressions[0].this
                    ref_table = expr.args.get("reference").find(exp.Table).name
                    ref_column = expr.args.get("reference").this.expressions[0].this
                    self.foreign_keys[table_name][local_column] = (
                        ref_table,
                        ref_column,
                    )
                    foreign_key = expr

            tables[table_name] = Table(
                this=exp.to_identifier(table_name),
                schema=Schema(expressions=column_defs),
                primary_key=primary_key,
                foreign_key=foreign_key,
                constraints=constraints,
            )
            if table_name not in dependency:
                dependency[table_name] = 0
            for local_column in self.foreign_keys[table_name]:
                from_table = self.foreign_keys[table_name][local_column][0]
                dependency[from_table] = dependency.get(from_table, 0) + 1

        sorted_table = OrderedDict(
            {
                tbl_name[0]: tables[tbl_name[0]]
                for tbl_name in sorted(
                    dependency.items(), key=lambda item: item[1], reverse=True
                )
            }
        )
        self.catalog = Catalog(tables=sorted_table)

    def _register_domains(self):
        """Register DomainSpec entries in `self.column_domain` for every column.

        This ensures the ColumnDomainPool knows the datatype, uniqueness and
        nullability for each logical column and can generate/track values.
        """
        for table_name, table in self.catalog.tables.items():
            for col in table.columns:
                unique = table.is_unique(col.name)
                nullable = table.nullable(col.name)
                ds = DomainSpec(
                    table_name=table_name,
                    column_name=col.name,
                    datatype=col.datatype,
                    unique=unique,
                    nullable=nullable,
                )
                self.column_domain.register_domain(ds)
        # Link pools for foreign keys so referenced and referencing columns share domain values
        try:
            for table_name, fks in self.foreign_keys.items():
                for local_col, (ref_table, ref_col) in fks.items():
                    try:
                        pool_local = self.column_domain.get_or_create_pool(
                            table_name, table_name, local_col
                        )
                        pool_ref = self.column_domain.get_or_create_pool(
                            ref_table, ref_table, ref_col
                        )
                        self.column_domain.add_equality(pool_ref, pool_local)
                    except Exception:
                        continue
        except Exception:
            pass

    def __repr__(self):
        return f"Instance(name={self.name}, tables={list(self.catalog.tables.keys())})"

    def get_rows(self, table_name) -> List[Row]:
        return self.data[table_name]

    def get_row(self, table_name, index):
        return self.data[table_name][index]

    def get_column_data(self, table_name, column_name):
        table_expr = self.catalog.get_table(table_name)
        column_index = -1
        for idx, column_def in enumerate(table_expr.columns):
            if column_def.name == column_name:
                column_index = idx
                break

        return [row[column_index] for row in self.data[table_name]]

    def create_row(
        self,
        table_name: str,
        values: Dict[str, Any] | None = None,
        alias: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a tuple to table and its dependent tables to maintain referential integrity.

        Args:
            table_name: Name of the table to expand
            values: Initial values for the new tuple

        Returns:
            Dict[str, Row]: Map of table names to their new tuples
        """

        values = values or {}
        new_tuples = defaultdict(list)
        positions: Dict[str, int] = {}
        table = self.catalog.get_table(table_name)

        fk_info = self.foreign_keys.get(table_name, {})
        referenced_tables = set()
        # Find missing foreign key values
        for local_col, (ref_table, ref_col) in fk_info.items():
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
                if not (table.is_unique(local_col_name) and val.concrete in used_values)
            ]
            if available_values:
                idx, chosen_value = random.choice(available_values)
                values[local_col_name] = chosen_value
            else:
                ref_values = {}
                # materialize referenced row so FK points to an actual tuple
                ref_position = self._create_row(ref_table_name, ref_values, alias=None)
                ref_value = self.get_column_data(ref_table_name, ref_col_name)[
                    ref_position
                ]
                values[local_col_name] = ref_value.concrete
                new_tuples[ref_table_name].append(
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
    ):
        """
        Internal helper method to create a row in a table with associated symbols.

        Args:
            table_name: Name of the table
            concretes: Concrete values for column in the table

        Returns:
            int: Index of the new row
        """
        table_expr = self.catalog.get_table(table_name)
        tuple_index = len(self.data[table_name])

        new_values = []
        for column_index, column_def in enumerate(table_expr.columns):
            datatype = column_def.datatype
            z_name = normalize_name(
                "%s_%s_%s_%s"
                % (table_name, column_def.name, str(datatype), tuple_index)
            )
            if column_def.name in concretes:
                concrete = concretes.get(column_def.name)
            else:
                # Use ColumnDomainPool's ValuePool when possible to sample/generate
                pool = self.column_domain.get_or_create_pool(
                    None, table_name, column_def.name
                )

                vals = pool.get_domain_values()
                if not vals:
                    pool.expand_domain(additional_samples=10)
                    vals = pool.get_domain_values()
                concrete = random.choice(list(vals))
                pool.domain.generated.append(concrete)
            z_value = Variable(z_name, dtype=datatype, concrete=concrete)
            new_values.append(z_value)
            self.symbols[z_name] = z_value
            self.symbol_to_table[z_name] = (table_name, column_def.name, column_index)
        rowid = "%s_rowid_%d" % (table_name, tuple_index)
        self.data[table_name].append(Row(rowid, *new_values))
        return tuple_index

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
        self.column_domain = ColumnDomainPool()
        self._register_domains()

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

            for table_name in self.catalog.tables:
                rows = self.get_rows(table_name)
                columns = []

                parameters = []
                for column in self.catalog.get_table(table_name).columns:
                    columns.append(f'"{column.name}"')
                    parameters.append(f":{normalize_name(column.name)}")
                mapped_data = []
                for row in rows:
                    data = {}
                    for column_name, column_value in zip(columns, row.columns):
                        data[normalize_name(column_name)] = column_value.concrete
                    mapped_data.append(data)
                if mapped_data:

                    column_list = ", ".join(columns)
                    stmt = f"INSERT INTO {table_name} ({column_list}) VALUES ({', '.join(parameters)})"
                    with open(f"tests/db/{self.name}_data_inserts.sql", "a") as f:
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

                    # logging.info(mapped_data)
                    conn.insert(stmt, mapped_data)
