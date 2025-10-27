from __future__ import annotations
from src.parseval.plan.rex import Table, Catalog, Schema, ColumnRef
from typing import Dict, Any, List, Optional
from sqlglot import parse, exp
from collections import OrderedDict, defaultdict
from .helper import normalize_name
from .symbol import *
from .faker import ValueGeneratorRegistry

from src.parseval.smt.domain import ColumnDomainPool, DomainSpec

import random, logging


class Instance:
    def __init__(
        self, ddls: str, name: str = "default", dialect: str = "sqlite"
    ) -> None:
        self.name = name
        self.ddls = ddls
        self.dialect = dialect
        self._build_catalog(ddls, dialect)
        self.data: Dict[str, List[Row]] = defaultdict(list)  # table_name -> List[Row]
        self.symbols = {}
        self.symbol_to_table = {}
        self.symbol_to_tuple_id = {}
        self.tuple_id_to_symbols = {}
        self.pk_fk_symbols = {}

        self.column_domain = ColumnDomainPool()

    def _build_catalog(self, ddls, dialect):
        ddls = parse(ddls, dialect=dialect)

        dependency, tables, foreign_keys = {}, OrderedDict(), {}
        foreign_keys = defaultdict(lambda: defaultdict(list))
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
                    foreign_keys[table_name][local_column] = (ref_table, ref_column)
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
            for local_column in foreign_keys[table_name]:
                from_table = foreign_keys[table_name][local_column][0]
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
        self.foreign_keys = foreign_keys

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

    def create_row(self, table_name: str, values: Dict[str, Any]) -> Dict[str, Row]:
        """
        Add a tuple to table and its dependent tables to maintain referential integrity.

        Args:
            table_name: Name of the table to expand
            values: Initial values for the new tuple

        Returns:
            Dict[str, Row]: Map of table names to their new tuples
        """

        new_tuples = defaultdict(list)
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
                if not (table.is_unique(local_col) and val.concrete in used_values)
            ]
            logging.info(
                f"available values for {ref_table_name}.{ref_col_name}: {available_values}"
            )
            if available_values:
                idx, chosen_value = random.choice(available_values)
                values[local_col] = chosen_value
            else:
                ref_values = {}
                ref_position = self._create_row(ref_table_name, ref_values)
                ref_value = self.get_column_data(ref_table_name, ref_col_name)[
                    ref_position
                ]

                values[local_col] = ref_value.concrete
                new_tuples[ref_table].append(self.get_row(ref_table_name, ref_position))
        # Step 2: Create the main row
        main_pos = self._create_row(table_name, values)
        new_tuples[table_name].append(self.get_row(table_name, main_pos))
        return new_tuples

    def _create_row(self, table_name: str, concretes: Dict):
        """
        Internal helper method to create a row in a table with associated symbols.

        Args:
            table_name: Name of the table
            values: Values for the row
            multiplicity: Multiplicity of the row

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
                concrete = self._assign_concrete_for_column(
                    table_name,
                    column_def.name,
                    str(datatype),
                    table_expr.is_unique(column_def.name),
                )
            z_value = Variable(z_name, dtype=datatype, concrete=concrete)
            new_values.append(z_value)
            self.symbols[z_name] = z_value
            self.symbol_to_table[z_name] = (table_name, column_def.name, column_index)
            # self.symbol_to_tuple_id[z_name] = tuple_index
            # self.tuple_id_to_symbols[tuple_index] = z_value
            # if table.is_unique(column_def) or table.is_foreignkey(column_def):
            #     self.pk_fk_symbols[z_name] = z_value
        self.data[table_name].append(Row(*new_values))
        return tuple_index

    def _assign_concrete_for_column(
        self, table_name, column_name, datatype: str, is_unique: bool = False
    ):
        """
        Generate a value for a column using the appropriate generator.

        Args:
            table_name: Name of the table
            column_name: Name of the column
            datatype: Type of the column (string or DataType)
            is_unique: Whether the value should be unique

        Returns:
            Any: A generated value for the column
        """
        existing_values = None
        if is_unique:
            table_expr = self.catalog.get_table(table_name)
            existing_values = set(
                d.concrete for d in self.get_column_data(table_expr.name, column_name)
            )

        generator = ValueGeneratorRegistry.get_generator(datatype)
        return generator(is_unique=is_unique, existing_values=existing_values)

    def to_db(
        self, host_or_path, database=None, port=None, username=None, password=None
    ):
        database = database or self.name
        database = database if database.endswith(".sqlite") else database + ".sqlite"
        from src.parseval.db_manager import DBManager

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
                    for column_name, column_value in zip(columns, row.args):
                        data[normalize_name(column_name)] = column_value.concrete
                    mapped_data.append(data)
                if mapped_data:

                    column_list = ", ".join(columns)
                    stmt = f"INSERT INTO {table_name} ({column_list}) VALUES ({', '.join(parameters)})"

                    # logging.info(mapped_data)
                    conn.insert(stmt, mapped_data)
