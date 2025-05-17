import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
import random, logging
from sqlalchemy import text

from concurrent.futures import ThreadPoolExecutor, wait
# logger = logging.getLogger('app')


from src.db.db_manager import DBManager

# DBManager().get_connection("loca")



def test_mysql_connection(dialect = 'mysql'):
    username = 'root'

    password = 'VeriEQL@2024'
    host = '172.19.0.2'
    database = 'counterexample_test'
    dbmanage = DBManager()

    scm = dbmanage.get_schema(host, database= database, username= username, password= password, dialect= 'mysql')
    # print(scm)



    # with dbmanage.get_connection(host_or_path= host, database= database, username= username, password= password, dialect= 'mysql') as conn:
    #     db_lists = conn.execute("SHOW DATABASES", fetch= '2')


    #     # db_lists = r.fetchall()
    # print(db_lists)

def test_sqlite_connection(dialect = 'sqlite'):
    host = './datasets/dev'
    database = 'counterexample_test.sqlite'
    dbmanage = DBManager()

    scm = dbmanage.get_schema(host, database= database, dialect= dialect)

    ...

def test_db_export(dialect = 'sqlite'):
    host = './datasets/dev'
    database = 'counterexample_test.sqlite'
    dbmanage = DBManager()
    dumps = dbmanage.export_database(host, database= database, dialect= dialect)
    print(dumps)

def test_mysqldb_export(dialect = 'mysql'):
    username = 'root'

    password = 'VeriEQL@2024'
    host = '172.19.0.2'
    database = 'counterexample_test'
    dbmanage = DBManager()
    dumps = dbmanage.export_database(host, database= database, username= username, password= password, dialect= dialect)
    print(dumps)


def test_dump_sqlite():
    DB_ROOT = "/home/chunyu/Projects/TestSuiteEval-main/database/gretel"
    from pathlib import Path
    dbmanager = DBManager()

    testcases = []
    import json

    success_cnt = []

    for db_palce in Path(DB_ROOT).iterdir():
        if not db_palce.is_dir():
            continue
        success_cnt.append(0)
        index = db_palce.stem[2:]
        for db_path in Path(db_palce).iterdir():
            try:
                if db_path.is_file() and str(db_path).endswith('sqlite') or str(db_path).endswith('db'):
                    host = db_path.parent
                    db_name = db_path.name
                    content = dbmanager.export_database(host_or_path= host, database= db_name)
                    # content.extend(dbmanager.export_database(host_or_path= host, database= db_name))
                    testcases.append(
                        {
                            'question_id' : index,
                            'instance': '\n'.join(content)
                        }
                    )
                    success_cnt[-1] = 1
            except Exception as e:
                print(f'error when {index} --> {db_path.name} --> {str(e)}')
    with open(f'gretel.json', 'w') as fp:
        json.dump(testcases, fp, indent= 4)
    print(sum(success_cnt))
    
test_dump_sqlite()

# test_mysql_connection()

# test_sqlite_connection()

# test_db_export()
# test_mysqldb_export()



# print(type(dbmanage.engines['abc']))

# host = "/./datasets/dev"

# print(dbmanage.get_schema(host, database))



# def evaluate(db_name):
#     with dbmanage.get_connection(host_or_path= host, database= db_name, username= username, password= password, dialect= 'mysql') as conn:
#         stmt = "SELECT COUNT(*) FROM information_schema.processlist WHERE COMMAND != 'Sleep'"

#         ddl = """CREATE TABLE IF NOT EXISTS employees (
#                 id INT PRIMARY KEY AUTO_INCREMENT,
#                 first_name VARCHAR(50) NOT NULL,
#                 last_name VARCHAR(50) NOT NULL,
#                 email VARCHAR(100),
#                 hire_date DATE
#             );"""

#         conn.create_tables(ddl)
#         r = conn.execute(stmt, fetch = 1)
#         # result = r.fetchone()
#         # if r:
#         #     print(f'current database connections: {r[0]}')
#         conn.drop_table('employees')

# # futures = []
# # with ThreadPoolExecutor(max_workers= 60) as pool:
#     for db in db_lists[:]:
#         db_name = db[0]
#         futures.append(pool.submit(evaluate, db_name))
# wait(futures)