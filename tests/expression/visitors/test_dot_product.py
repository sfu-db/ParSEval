import unittest
from src.expression.symbol.base import Variable, Literal, Binary
from src.expression.visitors.dot_product import extend_dot_product

class TestDotProductExtender(unittest.TestCase):
    def test_basic_dot_product(self):
        # Create two lists of variables
        list1 = [Variable(this="a", value=1), Variable(this="b", value=2)]
        list2 = [Variable(this="x", value=3), Variable(this="y", value=4)]
        
        # Create dot product expression
        dot_expr = Binary(key="Dot", left=list1, right=list2)
        
        # Extend the dot product
        extended = extend_dot_product(dot_expr)
        
        # Expected result: a*x + b*y
        self.assertEqual(extended.key, "Add")
        self.assertEqual(extended.left.key, "Mul")
        self.assertEqual(extended.left.left.this, "a")
        self.assertEqual(extended.left.right.this, "x")
        self.assertEqual(extended.right.key, "Mul")
        self.assertEqual(extended.right.left.this, "b")
        self.assertEqual(extended.right.right.this, "y")
    
    def test_dot_product_with_literals(self):
        # Create lists with literals
        list1 = [Literal.number(1), Literal.number(2)]
        list2 = [Literal.number(3), Literal.number(4)]
        
        # Create dot product expression
        dot_expr = Binary(key="Dot", left=list1, right=list2)
        
        # Extend the dot product
        extended = extend_dot_product(dot_expr)
        
        # Expected result: 1*3 + 2*4
        self.assertEqual(extended.key, "Add")
        self.assertEqual(extended.left.key, "Mul")
        self.assertEqual(extended.left.left.value, 1)
        self.assertEqual(extended.left.right.value, 3)
        self.assertEqual(extended.right.key, "Mul")
        self.assertEqual(extended.right.left.value, 2)
        self.assertEqual(extended.right.right.value, 4)
    
    def test_invalid_dot_product(self):
        # Test with non-list operands
        with self.assertRaises(ValueError):
            dot_expr = Binary(key="Dot", left=Variable(this="a"), right=Variable(this="b"))
            extend_dot_product(dot_expr)
        
        # Test with lists of different lengths
        with self.assertRaises(ValueError):
            list1 = [Variable(this="a"), Variable(this="b")]
            list2 = [Variable(this="x")]
            dot_expr = Binary(key="Dot", left=list1, right=list2)
            extend_dot_product(dot_expr)

if __name__ == '__main__':
    unittest.main() 