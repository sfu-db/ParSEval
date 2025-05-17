from typing import Dict, List, Tuple
import sqlite3, json, os

'''
Use as :
1. TEST_FILE_FP = test cases file path
2. evaluate(gold, pred, db_id, question_id)

Return:
    return a tuple(bool, str)
    the first value represent equal or not (0: NE, 1: EQ)
    the second value represent message why these two queries are NE(i.e. Count of Rows Diff, Query Results Diff)
'''

TMP_DIR = '' 
TEST_FILE_FP = './gretel.json'  
def get_instances(question_id):
    with open(TEST_FILE_FP) as fp:
        test_cases = json.load(fp)
    
    instances = []
    for instance in test_cases:
        if str(instance['question_id']) == str( question_id):
            instances.append(instance['instance'])
    return instances

def clean_workspace(fp):
    if os.path.exists(fp):
        os.remove(fp)

def evaluate(gold, pred, db_id, question_id) -> Tuple[bool, str]:
    instances = get_instances(question_id= question_id)
    passed = 0

    for instance_id, instance in enumerate(instances):
        db_path = os.path.join(TMP_DIR, f'{db_id}_{question_id}_{instance_id}.sqlite')
        clean_workspace(db_path)
        gold_res = []
        pred_res = []
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            for stmt in instance.split(';'):
                for e in stmt.split("\n\n\n"):
                    cursor.execute(e)
                # for e in parse(stmt, dialect= 'sqlite'):
                #     stmt = e.sql()
                #     print(stmt)
                #     cursor.execute(stmt)
            # cursor.executemany(instance)
            try:
                cursor.execute(pred)
                pred_res = cursor.fetchall()
            except Exception as e:
                return 0, 'PRED ERROR: %s' % str(e)

            try:
                cursor.execute(gold)
                gold_res = cursor.fetchall()
            except Exception as e:
                return 0, 'GOLD ERROR: %s' % str(e)
            
        if len(gold_res) != len(pred_res):
            return 0, 'rows diff'
        if set(gold_res) != set(pred_res):
            return 0, 'records diff'
        passed += 1
    
    return 1, len(instances)


# gold = "select * from language_preservation"
# pred =  "select * from language_preservation limit 1"

# eq, msg = evaluate(gold, pred, db_id= 'q48', question_id= 48)
