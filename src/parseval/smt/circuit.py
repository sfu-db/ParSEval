"""Per-encoder memoization and shared, definitionally constrained Z3 circuits."""

from functools import wraps

import z3


def memoized(method):
    """Cache only the bindings read by this term; never share across databases."""
    @wraps(method)
    def evaluate(self, term, environment):
        self.budget.tick()
        key = (method.__name__, term, self.environment_key(term, environment))
        if key not in self.cache:
            self.cache[key] = method(self, term, environment)
        return self.cache[key]
    return evaluate


class Circuit:
    def __init__(self, constraints: list[z3.BoolRef]) -> None:
        self.constraints = constraints
        self.nodes: dict[int, z3.ExprRef] = {}
        self.definitions: dict[int, z3.ExprRef] = {}

    def share(self, expression):
        expression = z3.simplify(expression)
        if expression.num_args() == 0:
            return expression
        identity = expression.get_id()
        if identity not in self.nodes:
            result = z3.FreshConst(expression.sort(), prefix="uexpr")
            self.constraints.append(result == expression)
            self.nodes[identity] = result
            self.definitions[result.get_id()] = expression
        return self.nodes[identity]

    def dependencies(self, expressions):
        pending = list(expressions)
        seen = set()
        constants = set()
        while pending:
            expression = pending.pop()
            identity = expression.get_id()
            if identity in seen:
                continue
            seen.add(identity)
            if identity in self.definitions:
                pending.append(self.definitions[identity])
            elif expression.num_args():
                pending.extend(expression.children())
            else:
                constants.add(identity)
        return constants
