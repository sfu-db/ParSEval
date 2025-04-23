from sqlglot import exp
from typing import Set, Union, Optional, Dict, Generator



import z3


def visit_z3_expr(
        e: Union[z3.ExprRef, z3.QuantifierRef],
        seen: Optional[Dict[Union[z3.ExprRef, z3.QuantifierRef], bool]] = None) -> \
        Generator[Union[z3.ExprRef, z3.QuantifierRef], None, None]:
    if seen is None:
        seen = {}
    elif e in seen:
        return
    seen[e] = True
    yield e
    if z3.is_app(e):
        for ch in e.children():
            for e in visit_z3_expr(ch, seen):
                yield e
        return
    if z3.is_quantifier(e):
        for e in visit_z3_expr(e.body(), seen):
            yield e
        return

def is_z3_var(e: z3.ExprRef) -> bool:
    return z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED
                
def get_all_vars(e: z3.ExprRef) -> Set[z3.ExprRef]:
    return {sub for sub in visit_z3_expr(e) if is_z3_var(sub)}


def rename_variable(expr, rename_map = None):
    if rename_map is None:
        rename_map = {}

    variables = get_all_vars(expr)
    for old_var in variables:
        if old_var not in rename_map:
            new_name = f"{old_var.decl().name()}_0"
            rename_map[old_var] = z3.Const(new_name, old_var.sort())

    return z3.substitute(expr, [(old, new) for old, new in rename_map.items()])
