
from typing import List
from src.corekit import DBManager
from sqlglot import exp
from collections import defaultdict
from .helper import generate_unique_value, convert, random_value_from_list


def to_db(instance, host_or_path, database, port = None, username = None, password = None, dialect = 'sqlite') -> List[str]:
     with DBManager().get_connection(host_or_path= host_or_path, database= database,
                                        port = port, username= username, password= password, dialect= dialect) as conn:
            conn.create_tables(*to_ddl(instance, dialect))
            inserts = to_insert(instance, dialect)
            for insert_stmt, data in inserts:
                conn.insert(insert_stmt, data)




def to_ddl(instance, dialect = 'sqlite') -> List[str]:
    stmts = []
    for table_name, table in instance._tables.items():
        column_defs = [c for c in table.column_defs]
        if table.primary_key and table.primary_key.expressions:
            column_defs.append(table.primary_key)
        column_defs.extend(table.foreign_keys)
        ddl = exp.Create(this = exp.Schema(this = exp.Table(this = exp.to_identifier(table_name, quoted= True)), expressions = column_defs), exists = True, kind = 'TABLE')
        stmts.append(ddl.sql(dialect= dialect))
    return stmts

                

def to_insert(instance, dialect = 'sqlite') -> List[str]:
    instance.commit()
    stmts = []
    data = defaultdict(lambda : defaultdict(list))

    for table_name, table in instance._tables.items():
        table_identifier = exp.to_identifier(table_name, quoted= True)
        columns = [exp.column(c.name) for c in table.column_defs]
        values = []
        for row in table:
            tup = []
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
                        from_table, from_column = instance._get_reference_table_column_names(table_name, column_def.name)
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
            stmts.append((stmt.sql(dialect= dialect), None))
    return stmts



