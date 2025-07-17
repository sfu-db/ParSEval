

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import DBManager, get_ctx
import unittest, logging


logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format='[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s',
)
class TestEncoder(unittest.TestCase):

    def add_constraint(self, constraints, label):
        if not isinstance(constraints, list):
            constraints = [constraints]

    def get_logical_paln(self, fp):
        from src.expression.query import parser
        parse = parser.QParser()
        plan = parse.explain_local(fp)
        return plan

    def get_instance(self, schema, name, values = {}, dialect = 'sqlite'):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        instance =Instance.create(schema= schema, name = name, dialect = dialect)
        # values = {
        #         'frpm': [
        #             {'Academic Year': "2024", "District Code": 16, 'CDSCode': "CDSCode1"},
        #             {'Academic Year': "2024", "District Code": 15},
        #             {'Academic Year': "2023", "District Code": 16},
        #             {'Academic Year': "2023", "District Code": 15}
        #         ],
        #         'satscores': [
        #             {'cds': "CDSCode1"}
        #         ]
        #     }
        if values:
            for tbl, value in values.items():
                for val in value:
                    instance.create_row(tbl, val)
        else:
            for tbl in instance._tables:
                instance.create_row(tbl, {})
        return instance
    
    def test_encode_spj(self):        
        from src.runtime.encoder import Encoder
        from src.runtime.uexpr_to_constraint import UExprToConstraint
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_sp")
        path = UExprToConstraint(lambda constraint,  label: self.add_constraint(constraint, label))

        plan = self.get_logical_paln("datasets/bird/plan/california_schools_7_gold.sql")
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)
        print(plan)
        # print(path.root_constraint)
        from src.runtime.to_dot import display_constraints
        print(display_constraints(path.root_constraint))

        print(f"leaves: {path.leaves.keys()}")

        for leaf, plause_child in path.leaves.items():
            print(f'leaf: {leaf}, plause: {plause_child}')
            print(f'tables: {plause_child.parent.get_tables()}')
            print(f'tuples: {plause_child.parent.get_all_tuples()}')


            print(f'involved table paths: {path._get_involved_tables_path(plause_child)}')
        
        # assert len(st.tbl_exprs) == 2
        assert st.tbl_exprs[0].nullable
        assert st.tbl_exprs[0].unique is False

    @unittest.skip(f'skip agg funcs dur to errors')
    def test_encode_agg_funcs(self):        
        from src.runtime.encoder import Encoder
        from src.runtime.uexpr_to_constraint import UExprToConstraint
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_sp")
        path = UExprToConstraint(lambda constraint,  label: self.add_constraint(constraint, label))
        plan = self.get_logical_paln("datasets/bird/plan/california_schools_6_gold.sql")
        print(plan)
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)
        
        # print(path.root_constraint)
        from src.runtime.to_dot import display_constraints
        print(display_constraints(path.root_constraint))

        print(f"leaves: {path.leaves.keys()}")

        for leaf, plause_child in path.leaves.items():
            print(f'leaf: {leaf}, plause: {plause_child}')
            print(f'tables: {plause_child.parent.get_tables()}')
            print(f'tuples: {plause_child.parent.get_all_tuples()}')
        
        assert len(st.tbl_exprs) == 2
        assert st.tbl_exprs[0].nullable
        assert st.tbl_exprs[0].unique is False


    # def test_encode_join_sort(self):        
    #     from src.runtime.encoder import Encoder
    #     with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
    #         schema = conn.get_schema()
    #     instance = self.get_instance(schema, "test_encode_join_sort")

    #     path = MockUexpr()
    #     plan = self.get_logical_paln("datasets/bird/plan/california_schools_4_gold.sql")
    #     encoder = Encoder(path)
    #     st = encoder(plan, instance = instance)


    #     assert len(st.tbl_exprs) == 2
    #     assert st.tbl_exprs[0].nullable
    #     assert st.tbl_exprs[0].unique is True

    # def test_encode_groupby_sort(self):        
    #     from src.runtime.encoder import Encoder
    #     with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
    #         schema = conn.get_schema()
    #     instance = self.get_instance(schema, "test_encode_join_sort")

    #     path = MockUexpr()
    #     plan = self.get_logical_paln("datasets/bird/plan/california_schools_31_gold.sql")
    #     encoder = Encoder(path)
    #     st = encoder(plan, instance = instance)
    #     assert len(st.tbl_exprs) == 2
    #     assert st.tbl_exprs[0].nullable is False
    #     assert st.tbl_exprs[0].unique is True

    #     assert st.tbl_exprs[1].nullable is False
    #     assert st.tbl_exprs[1].unique is False

    # def test_case_when(self):        
    #     from src.runtime.encoder import Encoder
    #     with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
    #         schema = conn.get_schema()
    #     instance = self.get_instance(schema, "test_encode_join_sort")

    #     path = MockUexpr()
    #     plan = self.get_logical_paln("datasets/bird/plan/california_schools_1_gold.sql")
    #     encoder = Encoder(path)
    #     st = encoder(plan, instance = instance)
    #     print(plan)
    #     print(st.tbl_exprs)
        # assert len(st.tbl_exprs) == 2
        # assert st.tbl_exprs[0].nullable is False
        # assert st.tbl_exprs[0].unique is True

        # assert st.tbl_exprs[1].nullable is False
        # assert st.tbl_exprs[1].unique is False

    # def test_encode_scalar(self):
    #     from src.runtime.encoder import Encoder
    #     with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
    #         schema = conn.get_schema()
    #     instance = self.get_instance(schema, "test_encode_join_sort")

    #     path = MockUexpr()
    #     plan = self.get_logical_paln("datasets/bird/plan/california_schools_9_gold.sql")
    #     encoder = Encoder(path)
    #     st = encoder(plan, instance = instance)
    
if __name__ == '__main__':
    # reset_folder('tests/db')
    # get_ctx(log_level = 'INFO')
    
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      