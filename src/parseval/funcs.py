from abc import ABC, abstractmethod


class Funcs(ABC):

    @abstractmethod
    @property
    def name(self):
        pass

    @abstractmethod
    def parse(self, data):
        pass

    @abstractmethod
    def evaluate(self, parsed_data):
        pass

    @abstractmethod
    def symbolic_representation(self, parsed_data):
        pass


from src.parseval.plan.planner import ExpressionRegistry, ExpressionEncoder

func = Funcs()
ExpressionRegistry._registry[func.name] = func.parse

ExpressionEncoder.SYMBOLIC_EVAL_REGISTRY[func.name] = func.symbolic_representation


# @ExpressionRegistry.register("OTHER_FUNCTION")
# def parse_other_function(planner, **kwargs) -> Expression:
#     operator = kwargs.pop("operator").upper()
#     operands = kwargs.pop("operands")

#     if operator == "STRFTIME":
#         _format = planner.walk(operands.pop())
#         operand = planner.walk(operands.pop())
#         return Strftime(args=[operand, _format], datatype=kwargs.pop("type"))
#     elif operator == "ABS":
#         operand = planner.walk(operands.pop())
#         return ABS(arg=operand, datatype=kwargs.pop("type"))

#     handler = ExpressionRegistry.get_handler(operator)
#     if handler:
#         return handler(planner, **kwargs)
#     else:
#         raise ValueError(f"Unsupported function: {operator}, {kwargs}")
