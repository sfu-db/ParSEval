'''Customized functions in ParSEval'''

from sqlglot import exp



class IntToStr(exp.Func):
    pass
class StrToInt(exp.Func):
    pass
class Length(exp.Func):
    pass

class Is_Null(exp.Unary, exp.Predicate):
    def __str__(self):
        return f"{self.this} IS NULL"

class Strftime(exp.Func):
    arg_types = {"this": True, "format": True, "culture": False}

class InStr(exp.Func):
    arg_types = {"this": True, "substring": True, "start": False}

class Julianday(exp.Func):
    arg_types = {"this": False, "expressions": False}

class Implies(exp.Condition):
    arg_types = {"this": True, "expression": True}

class Strftime(exp.Func):
    arg_types = {"this": True, "format": True, "culture": False}

