import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
from sqlglot import exp
from copy import deepcopy
import unittest

class TestExpression(unittest.TestCase):
    def test_dtype(self):
        from src.expression.types import DataType
        typ1 = DataType.build('Text')
        self.assertTrue(typ1.is_type(*DataType.TEXT_TYPES))

    def test_variable(self):
        from src.expression.symbol.base import Variable, convert, to_variable
        var_name = to_variable('Text', "name", 'alice')
        var_age = to_variable('Int', 'age', 15)
        cond1 = var_age > 15
        cond2 = var_age < 40
        cond3 = var_age > 20
        self.assertEqual(str(var_name), 'name')
        self.assertEqual(str(cond1.and_(cond2).and_(cond3)), "AND(GT(age, 15), LT(age, 40), GT(age, 20))")
        self.assertFalse(cond1.equals(cond2))
        self.assertTrue(cond1.equals(var_age > 15))

    def test_smt(self):
        from src.expression.symbol.base import Variable, convert, to_variable
        from src.expression.visitors.z3_visitor import Z3Visitor

        var_name = to_variable('Text', "name", 'alice')
        var_age = to_variable('Int', 'age', 15)
        cond1 = var_age > 15
        cond2 = var_age < 40
        cond3 = var_age > 20
        smt_visitor = Z3Visitor()
        cond5 = cond1.and_(cond2)
        
        import z3
        solver = z3.Solver()
        solver.add(cond5.accept(smt_visitor))
        self.assertEqual(solver.check(), z3.sat)
        
        print(solver.model())

    
    def test_substitution(self):
        from src.expression.symbol.base import Variable, convert, to_variable
        from src.expression.visitors import substitute

        var_age = to_variable('Int', 'age', 15)
        cond1 = var_age > 15
        cond2 = var_age < 40
        cond3 = var_age > 20
        
        cond5 = cond1.and_(cond2)
        substitutions = {
            var_age: Variable(this="a", value=5),
            cond2 : cond3
        }
        self.assertEqual(str(substitute(cond5, substitutions)), "AND(GT(a, 15), GT(a, 20))" )
        self.assertNotEqual(str(cond5), "AND(GT(a, 15), GT(a, 20))")
        substitute(cond5, substitutions, inplace= True)
        self.assertEqual(str(cond5), "AND(GT(a, 15), GT(a, 20))" )

        print(var_age.is_null())

        substitutions = {
            var_age: Variable(this="a", value=5)
        }
        print(substitute(var_age.is_null(), substitutions, inplace= False))

        print(substitute(var_age.is_null().not_(), substitutions, inplace= False))


    
    def test_path_builder(self):
        from src.expression.symbol.base import Variable, convert, to_variable
        from src.expression.visitors.predicate_tracker import PredicateTracker
        var_age = to_variable('Int', 'age', 15)
        cond1 = var_age > 15
        cond2 = var_age < 40
        cond3 = var_age > 20
        cond5 = cond1.and_(cond2)
        cond6 = cond5.not_()
        visitor = PredicateTracker()
        cond6.accept(visitor)

        self.assertEqual(len(visitor.predicates), 1)
        self.assertEqual(str(visitor.predicates[0]), "NOT (AND(GT(age, 15), LT(age, 40)))")

        visitor.reset()
        cond5.and_(cond3).accept(visitor)
        self.assertEqual(len(visitor.predicates), 3)
        self.assertListEqual(visitor.predicates, [cond1, cond2, cond3])


    def test_get_all_variables(self):
        from src.expression.symbol.base import Variable, convert, to_variable, get_all_variables
        var_age = to_variable('Int', 'age', 15)
        var_age2 = to_variable('Int', 'age2', 15)
        cond1 = var_age > 15
        cond2 = var_age2 < 40
        cond3 = var_age > 20
        
        cond5 = cond1.and_(cond2)

        print('===================')

        for v in get_all_variables(cond5):
            print(v, type(v))

    @unittest.skip("skip")
    def test_extend_summation(self):
        from src.expression.symbol.base import Variable, convert, to_variable, or_
        from src.expression.visitors import substitute, extend_summation

        left, right = [], []

        for i in range(2):
            left.append(to_variable('Int', f'left{i}', i))
        
        for j in range(2):
            right.append(to_variable('Int', f'right{j}', i))
        
        smt_exprs = []

        substitutions = {}

        left_replace = Variable(this = "a", value = 5)
        right_replace = Variable(this = "b", value = 10)

        substitutions[left[0]] = left_replace
        substitutions[right[0]] = right_replace

        for i in range(2):
            
            for j in range(2):
                
                smt_exprs.append(left[i] == right[j])
                
        predicate = or_(smt_exprs)
        print(predicate)

        for idx, (src, tar) in enumerate(substitutions.items()):
            substitution = {src : tar}
            print(substitution)
            predicate = extend_summation(predicate, substitution, extend = idx > 0)
            print(predicate)


        # for substitution in [{left[0]: Variable(this="a", value=5)}, {right[0]: Variable(this = 'b', value = 10)}]:
        #     predicate = extend_summation(predicate, substitution)

        print(predicate)

        # self.assertEqual(str(substitute(cond5, substitutions)), "AND(GT(a, 15), GT(a, 20))" )
        # self.assertNotEqual(str(cond5), "AND(GT(a, 15), GT(a, 20))")
        # substitute(cond5, substitutions, inplace= True)
        # self.assertEqual(str(cond5), "AND(GT(a, 15), GT(a, 20))" )
        

    
if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)