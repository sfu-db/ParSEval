from __future__ import annotations
''' adapt from sqllgot '''

import math
import typing as t

from sqlglot import alias, exp
import os
import logging
from sqlglot import parse_one, exp
from py4j.java_gateway import JavaGateway

logger = logging.getLogger('app')

def preprocess(sql, dialect = 'sqlite'):
    sql = sql.replace("'Ancestor''s Chosen'", "'Ancestors Chosen'").replace("Women's Soccer" , "Womens Soccer").replace("Ancestor's Chosen", "Ancestors Chosen") \
            .replace("SUM(T1.gender = 'M')", "SUM(CASE WHEN T1.gender = 'M' THEN 1 ELSE 0 END)") \
            .replace("SUM(T1.A2 = 'Decin')", "SUM(CASE WHEN T1.A2 = 'Decin' THEN 1 ELSE 0 END)") \
            .replace("SUM(status = 'C')", "SUM(CASE WHEN status = 'C' THEN 1 ELSE 0 END)") \
            .replace("SUM(type = 'gold')", "SUM(CASE WHEN type = 'gold' THEN 1 ELSE 0 END)") \
            .replace("SUM(type = 'gold')", "SUM(CASE WHEN type = 'gold' THEN 1 ELSE 0 END)") \
            .replace("SUM(T2.gender = 'F')", "SUM(CASE WHEN T2.gender = 'F' THEN 1 ELSE 0 END)") \
            .replace("SUM(type = 'Owner')", "SUM(CASE WHEN type = 'Owner' THEN 1 ELSE 0 END)") \
            .replace("SUM(type = 'Disponent')", "SUM(CASE WHEN type = 'Disponent' THEN 1 ELSE 0 END)") \
            .replace("SUM(T2.gender = 'F')", "SUM(CASE WHEN T2.gender = 'F' THEN 1 ELSE 0 END)")    \
            .replace("SUM(Currency = 'CZK')", "SUM(CASE WHEN Currency = 'CZK' THEN 1 ELSE 0 END)")    \
            .replace("SUM(Currency = 'EUR')", "SUM(CASE WHEN Currency = 'EUR' THEN 1 ELSE 0 END)")    \
            .replace("SUM(T2.gender = 'F')", "SUM(CASE WHEN T2.gender = 'F' THEN 1 ELSE 0 END)")    \
            .replace("Volkan Baǵa", "Volkan Baga") \
            .replace("AS per", "AS per1").replace("AS result", "AS result1").replace("DATETIME()", "CURRENT_TIMESTAMPE").replace("AS percent", "AS percent1") \
            .replace("datetime(CURRENT_TIMESTAMP, 'localtime')", "CURRENT_TIMESTAMP") \
            .replace("T2.RNP != '-' OR '+-'", "T2.RNP != '-' OR T2.RNP != '+-'") \
            .replace("T2.SSB = 'negative' OR '0'", "T2.SSB = 'negative' OR T2.SSB = '0'") \
            .replace(" Date('now')", "CURRENT_TIMESTAMP") \
            .replace("Huitième édition", 'Huitieme edition') \
            .replace('Ola de frío', 'Ola de frio') \
            .replace("SELECT Title, title FROM Cartoon ORDER BY title", "SELECT Title as Title1, title FROM Cartoon ORDER BY title")

    query_expression = parse_one(sql, read= dialect)
    def transform_keywords(node):
        if isinstance(node, exp.Identifier):
            if node.this.lower() in ["year", "date", "matches", "language", "result", "show", "time", 'power', 'element' , 'free', \
                              'count', 'position', 'match', 'member', 'translation', 'rank', 'percent', 'timestamp']:
                node.args['quoted'] = True
                # return exp.maybe_parse(f'"{node.this}"', dialect= dialect)
                # return parse_one(f'"{node.this}"')
            # elif node.args['quoted'] == True:
            #     return exp.maybe_parse(f'{node.this}')
            # parse_one(f"'{node.this}'")
        return node
    def transform_standard(node):
        if isinstance(node, exp.Select) and not isinstance(node.parent, exp.Subquery):
            is_agg = False
            for projection in node.expressions:
                if isinstance(projection, exp.AggFunc) or isinstance(projection.this, exp.AggFunc):
                    is_agg = True
            if is_agg:
                g_ = node.args.get('group')
                if g_ is None:
                    g_ = exp.Group(expressions = [])
                    g_.parent = node
                for projection in node.expressions:
                    if not (isinstance(projection, exp.AggFunc) or isinstance(projection.this, exp.AggFunc)):
                        n = projection.this  if  isinstance(projection, exp.Alias) else projection
                        if n not in g_.expressions:
                            if 'expressions' not in g_.args:
                                g_.args['expressions'] = []
                            g_.args['expressions'].append(n)
                if g_.expressions:
                    node.args['group'] = g_

            order = node.args.get("order")
            if order:
                projections = []
                alias_or_names = []                
                for projection in node.expressions:
                    projections.append(projection)
                    alias_or_names.append(projection.alias_or_name)
                
                for col in order.expressions:                    
                    flag = False
                    for ref in node.expressions:
                        r = ref.this if isinstance(ref, exp.Alias) else ref
                        if col.this == r:
                            flag = True                    
                    if not flag:
                        node.expressions.append(col)
                    # if col.this not in projections and col.this.alias_or_name not in alias_or_names:
                    #     node.expressions.append(col)
                    #     projections.append(col)
        ### make sure all cols are grouped
        if isinstance(node, exp.Group):
            parent = node.parent_select
            columns_in_group = []
            for group_column_name in node.expressions:
                columns_in_group.append(group_column_name.alias_or_name)
            for projection in parent.expressions:
                if isinstance(projection, exp.Column) and projection.alias_or_name not in columns_in_group:
                    node.expressions.append(projection)
                    columns_in_group.append(projection.alias_or_name)
            return node
        if isinstance(node, exp.Date) and dialect == 'sqlite':
            return parse_one(f'UDATE({node.this})')
        if isinstance(node, exp.If):
            if not isinstance(node.parent, exp.Case):
                then_branch = node.args.get("true")
                else_branch = node.args.get('false')            
                return parse_one(f'CASE WHEN {node.this} THEN {then_branch} ELSE {else_branch} END')
        return node
    expression_tree = query_expression.transform(transform_standard)
    expression_tree = expression_tree.transform(transform_keywords)
    return expression_tree.sql(dialect= dialect)

## FOR SPIDER/BIRD FORMAT
DB_ID = "db_id"
TABLE_NAMES = "table_names_original"
COLUMN_NAMES = "column_names_original"
COLUMN_TYPES = "column_types"
PRIMARY_KEYS = "primary_keys"
FOREIGN_KEYS = "foreign_keys"

from typing import List
from dataclasses import dataclass, asdict, field



FUNCTION_DEF = [
    {
        "type": "SCALAR",
        "identifier": "STRFTIME",
        "parameters": ["DATE", "STRING"],
        "return_type": "STRING"
      },
      {
        "type": "SCALAR",
        "identifier": "UDATE",
        "parameters": ["TIMESTAMP"],
        "return_type": "DATE"
      },
      {
        "type": "SCALAR",
        "identifier": "DATETIME",
        "parameters": ["STRING"],
        "return_type": "DATE"
      },
      {
        "type": "SCALAR",
        "identifier": "INSTR",
        "parameters": ["STRING", "STRING"],
        "return_type": "INTEGER"
      }, {
        "type": "SCALAR",
        "identifier": "JULIANDAY",
        "parameters": ["DATE"],
        "return_type": "INTEGER"
      }, {
        "type": "SCALAR",
        "identifier": "LENGTH",
        "parameters": ["STRING"],
        "return_type": "INTEGER"
      }, {
        "type": "SCALAR",
        "identifier": "NOW",
        "parameters": [],
        "return_type": "TIMESTAMP"
      }, {
        "type": "SCALAR",
        "identifier": "SUBSTR",
        "parameters": ["STRING", "INTEGER", "INTEGER"],
        "return_type": "STRING"
      }, {
        "type": "SCALAR",
        "identifier": "SUBSTR",
        "parameters": ["STRING", "INTEGER"],
        "return_type": "STRING"
      }, {
        "type": "SCALAR",
        "identifier": "B1",
        "parameters": ["INTEGER"],
        "return_type": "BOOLEAN"
      }
      , {
        "type": "SCALAR",
        "identifier": "B2",
        "parameters": ["INTEGER"],
        "return_type": "BOOLEAN"
      }, {
        "type": "SCALAR",
        "identifier": "B",
        "parameters": ["INTEGER"],
        "return_type": "BOOLEAN"
      }
]

def _remove_foreign_key(sql, dialect = 'sqlite'):
    def transformer(node):
        if isinstance(node, exp.ForeignKey):
            return None
        return node
    expr = parse_one(sql, dialect= dialect)
    transformed_tree = expr.transform(transformer)
    return transformed_tree.sql()

def get_logical_plan(ddl: List[str], queries: List[str], function_defs: List[str] = None, dialect = 'sqlite'):
    function_defs =FUNCTION_DEF if function_defs is None else function_defs
    request = {
        'ddl': [_remove_foreign_key(d, dialect= dialect) for d in ddl],
        'queries': [preprocess(sql, dialect = dialect) for sql in queries],
        'functions': function_defs
    }
    gateway = JavaGateway()
    plan = gateway.entry_point.parse(str(request))
    return plan
        

def save_plan(schema, query, raw, folder_path = 'datasets/gold', sqlfile_name = None, gold = ''):
    # Check if the folder path exists
    if not os.path.exists(folder_path):
        print(f"The specified folder '{folder_path}' does not exist.")
        return
    if sqlfile_name:
        new_file_name = sqlfile_name
    else:
        # Create a new file named after the count of files + 1
        files_in_folder = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
        total_files = len(files_in_folder)        
        new_file_name = f"plan_{total_files + 1}.sql"

    new_file_path = os.path.join(folder_path, new_file_name)
    with open(new_file_path, 'w') as fp:
        fp.write(f'-- SCHEMA: {schema}\n')
        fp.write(f'-- SRC: {gold}\n')
        fp.write(f'-- CLEANED: {query}\n')        
        fp.write(f'{raw}')
