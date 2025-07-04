class Predicate:
    def __init__(self, expr, result):
        
        self.expr = expr
        self.result = result
    
    def __eq__(self, other):

        if isinstance(other, Predicate):
            res = self.result == other.result
            return res
        else:
            return False

    def negate(self):
        """Negates the current predicate"""
        assert (self.result is not None)
        return Predicate(self.expr, not self.result)
    
    def __str__(self):
        return f'Predicate({self.expr}, {self.result})'
    
    def __bool__(self):
        return self.result
    
    def __repr__(self):
        return str(self)