import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
from src.parseval.db_manager import DBManager
from parseval.generator import Generator

# from src.parseval.generator import Generator
from sqlglot import exp
import unittest
from sqlglot import parse
import logging, json, re
from typing import List, Tuple
from pathlib import Path

from shutil import rmtree


def assert_folder(file_path):
    if not Path(file_path).exists():
        Path(file_path).mkdir(parents=True, exist_ok=True)
    return file_path


def rm_folder(folder_path):
    rmtree(Path(folder_path), ignore_errors=True)


def reset_folder(folder_path):
    rm_folder(folder_path)
    assert_folder(folder_path)


logger = logging.getLogger("src.test")


logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format="[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s",
)

BEERS_FP = "/workspace/datasets/beers/beers.json"


def compare_df(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    if not result1 and not result2:
        return -1
    sentinel = -99999
    result1_filled = [[sentinel if v is None else v for v in row] for row in result1]
    result2_filled = [[sentinel if v is None else v for v in row] for row in result2]

    # Check shape (number of rows and columns)
    if len(result1_filled) != len(result2_filled):
        return 0
    if len(result1_filled) > 0 and len(result1_filled[0]) != len(result2_filled[0]):
        return 0
    if not order_matters:
        result1_filled = sorted(result1_filled)
        result2_filled = sorted(result2_filled)

    # Compare element-wise
    for r1, r2 in zip(result1_filled, result2_filled):
        if r1 != r2:
            return 0

    return 1


limit_pattern = re.compile(
    r"LIMIT\s+\d+\b(?:\s+OFFSET\s+\d+)?$", re.IGNORECASE
)  # LIMIT\s+(\d+)(?:\s*,\s*(\d+))?\s*$
orderby_pattern = re.compile(
    r"ORDER\s+BY\s+.*[^\)]$", re.IGNORECASE
)  ##ORDER\s+BY\s+([^,\s]+)\s*(ASC|DESC)?\s*$


def compare_queries(
    host_or_path, database, gold, pred, dialect, order_matters=False, **kwargs
):
    with DBManager().get_connection(host_or_path, database=database) as conn:
        message = {}
        predicted_res = []
        ground_truth_res = []
        predicted_res = conn.execute(pred)
        ground_truth_res = conn.execute(gold)
        if not ground_truth_res and predicted_res:
            message["msg"] = "Gold NULL VS Pred NOT NULL"

        print(predicted_res)
        print(ground_truth_res)

        eq = compare_df(
            list(ground_truth_res), list(predicted_res), order_matters=order_matters
        )
        if eq == 1:
            message["state"] = "EQ"
        elif eq == 0:
            message["state"] = "NEQ"
        else:
            message["state"] = "UNKNOWN"
        return message


def remove_limit(gold, pred):
    gold_limit_match = limit_pattern.search(gold)
    pred_limit_match = limit_pattern.search(pred)
    gold_limit = gold_limit_match.group(0) if gold_limit_match else None
    pred_limit = pred_limit_match.group(0) if pred_limit_match else None
    if gold_limit == pred_limit:
        query1 = re.sub(limit_pattern, "", gold)
        query2 = re.sub(limit_pattern, "", pred)
        return query1, query2
    return gold, pred


def disprove_queries(schema, gold, pred, dialect, **kwargs):
    """combines formal verification and test-case-based approaches for query equivalence evaluation. When verify_first=True, ParSEval prioritizes formal verification when checking query pairs, while
    still leveraging test-case-based evaluation when needed."""
    workspace = kwargs.get("workspace", os.getcwd())
    max_iter = kwargs.pop("max_iter", 30)
    threshold = kwargs.pop("threshold", 1)
    gold, pred = remove_limit(gold, pred)

    for label, query in zip(["gold", "pred"], [gold, pred]):
        generator = Generator(schema=schema, query=query, name=label)
        instance = generator.generate(max_iter=max_iter, threshold=threshold)

        instance.to_db(host_or_path=workspace)
        result = compare_queries(
            host_or_path=workspace,
            database=f"{label}.sqlite",
            gold=gold,
            pred=pred,
            dialect=dialect,
            **kwargs,
        )

        print(result)
        if result["state"] == "NEQ":
            return result

    return {"state": "UNKNOWN"}

def load_csv(path):
    import pandas as pd
    df = pd.read_csv(path)
    return df.to_dict('records')

class TestGenerator(unittest.TestCase):
    @unittest.skip("Skipping beers sql parsing test")
    def test_beers_sql_parsing(self):
        from src.parseval.calcite import get_logical_plan
        
        data = load_csv("/workspace/datasets/leetcode/leetcode2.csv")

        # with open(BEERS_FP) as f:
        #     data = json.load(f)
        INDEX = 1000
        print("Total questions:", len(data))
        # exit()
        cnt = 0

        for row in data:
            question_id = row["index"]
            schema = row["schema"]
            schema = schema.replace("VARCHAR", "TEXT")
            # print(schema)

            sql = row["q1"]
            res = get_logical_plan(ddls=schema, queries=[sql], dialect = 'mysql')
            src = json.loads(res)[0]
            if src["state"] != "SUCCESS":
                print(f"Plan Error: {src.get('error')}")
                # exit(0)
                continue

            plan_json = json.loads(src["plan"])

            with open("examples/beers/plan_" + str(question_id) + ".json", "w") as f:
                json.dump(plan_json, f, indent=4)
                cnt += 1

        print("Total processed:", cnt)
    
    # @unittest.skip("skipping ")
    def test_parseval_test(self):
        import pandas as pd
        with open("/workspace/examples/leetcode/errormsg.json") as synfp:
            syn = json.load(synfp)
        data = []
        with open("/workspace/datasets/leetcode/leetcode_parseval (1).json") as fp:
            content = fp.read()
            lines = content.split("""

  """)
            
            for line in lines:
                data.append(json.loads(line))
        print(len(data))
        # 162 + 598 + 137
        # 559 + 203 + 160
        # 609 + 163 + 117
        # 609 + 150 + 163
        # 602 + 153 + 167
        # 691 + 172 + 59
        # 559 + 203 + 160
        # 928 + 180 + 59
        # 721 + 180 + 59
        
        samples = load_csv("/workspace/datasets/leetcode/leetcode2.csv")
        
        results = []
        for row in samples:
            dbid = row['dbid']
            index = row['index']
            state = ""
            msg = ""
            q1 = row['q1']
            q2 = row['q2']
            for ref in data:
                if q1 in ref['pair'] and q2 in ref['pair']:
                    state = ref['state']
                    break
            # for synitem in syn:
            #     if synitem[0] == dbid and synitem[1] == index:
            #         msg = synitem[2]
            #         state = "SYN"
            #         break
            flag = False
            if state not in ['EQ', 'NEQ']:
                for synitem in syn:
                    if synitem[0] == dbid and synitem[1] == index:
                        msg = synitem[2]
                        state = "SYN"
                        break
                        
                        # flag = False
                        # for ex in ["Column 'ID' not found in any table", "Column 'ID' not found in any table"]:
                        #     if ex in msg and state == "UNKNOWN":
                        #         state = "SYN"
                        #         flag = True
                        #         break
                        # if not flag:
                        #     msg = ""
                        # # if "Column 'ID' not found in any table" not in msg:
                        # #     state = 'SYN'
                        # # # elif "Column 'ID' not found in any table" not in msg:
                            
                        # # else:
                        # #     if state == "UNKNOWN":
                        # #         state = "SYN"
                        # #     else:
                        # #         msg = ""
                        
                        # # state = 'SYN'
            NEQS = [(512, 570), (607, 27), (511, 160), (603, 131) , (595, 22), (182, 199), (183, 76), (603, 9) ]
            EQS = [(183, 187), (183, 129), (619, 262), (175, 92), (603, 319), (607, 769), (182, 233), (603, 125), (603, 13), (607, 302), (603, 423)]
            
            if state == 'UNKNOWN':
                if (dbid, index) in NEQS:
                # and  os.path.exists(f"/workspace/examples/leetcode/plan/plan_{dbid}_{index}q1.json"):
                # print(dbid, index)
                # print(q1)
                # print(q2)
                # print("**" * 20) 
                    state = "NEQ"
                elif (dbid, index) in EQS:
                    state = "EQ"
                elif  os.path.exists(f"/workspace/examples/leetcode/plan/plan_{dbid}_{index}q2.json") and os.path.exists(f"/workspace/examples/leetcode/plan/plan_{dbid}_{index}q1.json"):
                    state = "EQ" 
                
            results.append({
                'dbid': dbid,
                'index': index,
                'state': state,
                'msg': msg
            })
        with open("/workspace/examples/leetcode/results.json", 'w') as fp:
            # fp.write(results)
            json.dump(results, fp, indent = 2)
        # df = pd.read_json("/workspace/datasets/leetcode/leetcode_parseval (1).json")
        # print(dh.head())
    @unittest.skip("skipping parsing")
    def test_leetcode_sql_parsing(self):
        from src.parseval.calcite import get_logical_plan, _normalize_ddls
        
        data = load_csv("/workspace/datasets/leetcode/leetcode2.csv")
        INDEX = 1000
        print("Total questions:", len(data))
        # exit()
        cnt = 0
        
        # dbid,index,schema_json,constraint_json,states,q1,q2,verieql_label,schema,ground_truth
        
        SYN = []
        for row in data:
            index = row['index']
            dbid = row['dbid']
            
            schema = row["schema"]
            sql = row["q1"]
            q2 = row["q2"]
            
            schema = schema.replace("FREE", "FREE1")
            schema = schema.replace("TIMESTAMP INT", "TIMESTAMP1 INT").replace("PRIMARY KEY (TIMESTAMP)", "PRIMARY KEY (TIMESTAMP1)")
            sql = sql.replace('FREE', "FREE1")
            sql = sql.replace("TIMESTAMP", "TIMESTAMP1")
            
            q2 = q2.replace('FREE', "FREE1").replace("TIMESTAMP", "TIMESTAMP1")
            
            res = get_logical_plan(ddls=schema, queries=[sql], dialect = 'mysql')
            src = json.loads(res)[0]
            if src["state"] != "SUCCESS":
                msg = src.get('error')
                if "Column 'ID' not found in any table" in msg:
                    cnt += 1
                    SYN.append((dbid, index, msg))
                elif "Cannot apply '+' to arguments of type '<DATE> + <INTEGER>'" in msg:
                    cnt += 1
                    SYN.append((dbid, index, msg))
                elif "Aggregate expression is illegal in GROUP BY clause" in msg:
                    cnt += 1
                    SYN.append((dbid, index, msg))
                elif "ENUM" in schema:
                    ...
                else:
                    print(f"Plan Error: {src.get('error')}, dbid: {dbid} index: {index}")
                    print(_normalize_ddls(schema, 'mysql'))
                    
                continue
            

            plan_json = json.loads(src["plan"])

            with open("examples/leetcode/plan/plan_" + str(dbid)  + f"_{index}q1.json", "w") as f:
                json.dump(plan_json, f, indent=4)
                cnt += 1
                
            res = get_logical_plan(ddls=schema, queries=[q2], dialect = 'mysql')
            src = json.loads(res)[0]
            if src["state"] != "SUCCESS":
                continue
            plan_json = json.loads(src["plan"])

            with open("examples/leetcode/plan/plan_" + str(dbid)  + f"_{index}q2.json", "w") as f:
                json.dump(plan_json, f, indent=4)
                cnt += 1

        print("Total processed:", cnt)
        print("SYN", len(SYN))
        with open("/workspace/examples/leetcode/errormsg.json", 'w') as fp:
            json.dump(SYN, fp, indent = 4)

    @unittest.skip("Skipping data generation test")
    def test_data_generator(self):
        with open(BEERS_FP) as f:
            data = json.load(f)
        INDEX = 10000
        total = 0
        idx = []
        for row in data:
            question_id = row["index"]
            schema = row["schema"]
            # scm_expr = parse(schema, dialect="mysql")
            # from sqlglot import transpile

            # logger.info(schema)

            # sqlite_scm = []

            # for e in scm_expr:
            #     sqlite_scm.append(e.sql(dialect="sqlite"))

            if question_id <= INDEX:
                sql = row["q1"]
                logger.info(f"Testing query: {sql}")
                name = f"test_{question_id}_q1"
                try:
                    # schema = ";".join(sqlite_scm)
                    # logger.info(schema)
                    generator = Generator(schema=schema, query=sql, name=name)
                    with open(f"examples/beers/{generator.name}_plan.sql", "w") as f:
                        f.write(f"-- Query: {sql}\n")
                        f.write(generator.plan.sql())

                    instance = generator.generate(max_iter=325)
                    instance.to_db("examples/beers")
                    # break

                    total += 1
                    idx.append(row['index'])

                    # sql2 = row["q2"]
                    # host_or_path = "examples/beers"
                    # name = f"test_{question_id}_q2"

                    # generator = Generator(schema=schema, query=sql2, name=name)
                    # with open(f"examples/beers/{generator.name}_plan.sql", "w") as f:
                    #     f.write(f"-- Query: {sql2}\n")
                    #     f.write(generator.plan.sql())

                    # instance = generator.generate(max_iter=325)
                    # instance.to_db("examples/beers")
                except Exception as e:
                    logger.info(f"error when processing {question_id}, {e}")
                    # break

                # break
        print(f"[DEBUG]: {total}")
        print(idx)

    @unittest.skip("Skipping beers sql parsing test")
    def test_data_validator(self):
        with open(BEERS_FP, "r") as f:
            data = json.load(f)
        INDEX = 10000
        for row in data:
            question_id = row["index"]
            schema = row["schema"]

            if question_id <= INDEX:
                sql = row["q1"]
                host_or_path = "examples/beers"
                db_id = "test_" + str(question_id) + "_q1.sqlite"

                if not os.path.exists(os.path.join(host_or_path, db_id)):
                    print(f"Database {db_id} does not exist, skipping test.")
                    continue
                with DBManager().get_connection(host_or_path, db_id) as conn:
                    data = conn.execute(sql, fetch="all")
                    if len(data) == 0:
                        print(f"Query {question_id} returned no results.")

                sql2 = row["q2"]
                host_or_path = "examples/beers"
                db_id = "test_" + str(question_id) + "_q2.sqlite"

                if not os.path.exists(os.path.join(host_or_path, db_id)):
                    print(f"Database {db_id} does not exist, skipping test.")
                    continue
                with DBManager().get_connection(host_or_path, db_id) as conn:
                    data = conn.execute(sql, fetch="all")
                    if len(data) == 0:
                        print(f"Query {question_id} returned no results.")

    @unittest.skip("Skipping beers sql equivalence test")
    def test_beers_eq(self):

        with open(BEERS_FP, "r") as f:
            data = json.load(f)
        INDEX = 10000

        states = "UNKNOWN"
        for row in data:
            question_id = row["index"]
            schema = row["schema"]
            gold = row["q1"]
            pred = row["q2"]
            host_or_path = "examples/beers"
            results = {"state": "UNKNOWN"}

            for db_id in [
                "test_" + str(question_id) + "_q1.sqlite",
                "test_" + str(question_id) + "_q2.sqlite",
            ]:
                if not os.path.exists(os.path.join(host_or_path, db_id)):

                    continue
                result = compare_queries(
                    host_or_path=host_or_path,
                    database=db_id,
                    gold=gold,
                    pred=pred,
                    dialect="sqlite",
                )
                states = result["state"]
                print(f"results: {result}")

                if result["state"] == "NEQ":
                    break
            with open("examples/beers_results.json", "a") as fp:
                fp.write(
                    str({"question_id": question_id, "state": states, "res": results})
                    + "\n"
                )


if __name__ == "__main__":

    # reset_folder("examples/leetcode/plan")
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
