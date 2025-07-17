

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import DBManager
import unittest


class MockUexpr:
    def advance(self, *args, **kwargs):
        return
    def which_branch(self, *args, **kwargs):
        return
class TestEncoder(unittest.TestCase):
    def get_logical_paln(self, fp):
        from src.expression.query import parser
        parse = parser.QParser()
        plan = parse.explain_local(fp)
        return plan

    def get_instance(self, schema, name, dialect = 'sqlite'):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        instance =Instance.create(schema= schema, name = name, dialect = dialect)
        for tbl in instance._tables:
            instance.create_row(tbl, {})
        return instance
    
    def test_encode_spj(self):        
        from src.runtime.encoder import Encoder
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_sp")

        path = MockUexpr()
        plan = self.get_logical_paln("datasets/bird/plan/california_schools_24_gold.sql")
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)

        assert len(st.tbl_exprs) == 2
        assert st.tbl_exprs[0].nullable
        assert st.tbl_exprs[0].unique is False

    def test_encode_agg_funcs(self):        
        from src.runtime.encoder import Encoder
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_groupby")

        path = MockUexpr()
        plan = self.get_logical_paln("datasets/bird/plan/california_schools_6_gold.sql")
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)
        # print(plan)
        # print(st.tbl_exprs)
        assert st.tbl_exprs[0].unique is False

    def test_encode_join_sort(self):        
        from src.runtime.encoder import Encoder
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_join_sort")

        path = MockUexpr()
        plan = self.get_logical_paln("datasets/bird/plan/california_schools_4_gold.sql")
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)


        assert len(st.tbl_exprs) == 2
        assert st.tbl_exprs[0].nullable
        assert st.tbl_exprs[0].unique is True

    def test_encode_groupby_sort(self):        
        from src.runtime.encoder import Encoder
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_join_sort")

        path = MockUexpr()
        plan = self.get_logical_paln("datasets/bird/plan/california_schools_31_gold.sql")
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)
        assert len(st.tbl_exprs) == 2
        assert st.tbl_exprs[0].nullable is False
        assert st.tbl_exprs[0].unique is True

        assert st.tbl_exprs[1].nullable is False
        assert st.tbl_exprs[1].unique is False

    def test_case_when(self):        
        from src.runtime.encoder import Encoder
        with DBManager().get_connection("examples", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        instance = self.get_instance(schema, "test_encode_join_sort")

        path = MockUexpr()
        plan = self.get_logical_paln("datasets/bird/plan/california_schools_1_gold.sql")
        encoder = Encoder(path)
        st = encoder(plan, instance = instance)
        print(plan)
        print(st.tbl_exprs)
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
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      