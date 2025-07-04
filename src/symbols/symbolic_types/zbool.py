

from src.symbols.ztype import ZType

class ZBool(ZType):
    def __init__(self, expr=None, value=None) -> None:       
        super().__init__(expr, value)

    def __str__(self):
        return "{%s, value: %s, expr: %s)" % (self.dtype, self.value, self.expr)
    

    def __bool__(self):
        ...

    # def __bool__(self):
	# 	ret = bool(self.getConcrValue())
	# 	if SymbolicObject.SI != None:
	# 		SymbolicObject.SI.whichBranch(ret, self)
	# 	return ret