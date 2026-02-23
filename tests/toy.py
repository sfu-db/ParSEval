
from typing import Dict

# from parseval.plan.rex import *

# def transform(condition: exp.Expression, ctx: Dict):
#     if isinstance(condition, exp.Predicate):
#         ctx.setdefault("smt_conditions", []).append(condition)
#     if isinstance(condition, exp.Column):
#         column = condition
#         table = column.table
#         column_name = column.name
#         row = ctx.get("row", {})
#         smt_expr = row[column_name]
#         ctx.setdefault("mapping", {})[smt_expr] = condition
#         return smt_expr
    
#     elif isinstance(condition, exp.Literal):
#         value = condition.this
#         datatype = condition.type
#         try:
#             if datatype.is_type(*exp.DataType.INTEGER_TYPES):
#                 value = int(value)
#                 return exp.Literal.number(value)
#             elif datatype.is_type(*exp.DataType.REAL_TYPES):
#                 value = float(value)
#                 return exp.Literal.number(value)
#             elif datatype.is_type(exp.DataType.Type.BOOLEAN):
#                 value = bool(value)
#             elif datatype.is_type(*exp.DataType.TEMPORAL_TYPES):
#                 from datetime import datetime
               
#             elif datatype.is_type(*exp.DataType.TEXT_TYPES):
#                 value = str(value)
#             else:
#                 raise ValueError(f"Unsupported datatype: {datatype}")
#         except Exception as e:
#             value = None
#         condition.set('concrete', value)
#         condition.type = datatype
        
        
        
#         return condition
#     elif isinstance(condition, exp.Cast):
#         to_type = condition.to
#         inner = condition.this
#         if isinstance(condition.this, exp.Column):
#             ctx.setdefault("datatype", {})[condition.this] = to_type
#         inner.type = to_type
#         return inner
#     elif isinstance(condition, exp.Case):
#         for when in condition.args.get("ifs"):
#             print(f'when: {when}')
#             smt_expr = when.this
#             smt_expr = when.this.transform(transform, copy = True, ctx = ctx)
#             if smt_expr.concrete:
#                 return when.args.get("true").transform(transform, copy = True, ctx = ctx)
#         return condition.args.get("default").transform(transform, copy = True, ctx = ctx)
#     return condition



# column_1 = exp.Column(this="age", table="users", _type=exp.DataType.build("INT"))
# column_2 = exp.Column(this="salary", table="users", _type=exp.DataType.build("REAL"))
# column_3 = exp.Column(this="name", table="users", _type=exp.DataType.build("TEXT"))


# row = {
#     "age": Variable(this = "age1", concrete = 30, _type=exp.DataType.build("INT")),
#     "salary": Variable(this = "salary1", concrete = 50000.0, _type=exp.DataType.build("REAL")),
#     "name": Variable(this = "name1", concrete = "Bob", _type=exp.DataType.build("TEXT"))
# }

# condition = exp.And(
#     this=exp.GT(this=column_1, expression=exp.Literal(this="25", _type=exp.DataType.build("INT"), is_string = False)),
#     expression=exp.Or(
#         this=exp.LT(this=column_2, expression=exp.Literal(this="60000.0", _type=exp.DataType.build("REAL"), is_string = False)),
#         expression=exp.EQ(this=column_3, expression=exp.Literal(this="Alice", _type=exp.DataType.build("TEXT"), is_string = True))
#     )
# )
# condition = exp.GT(this=column_1, expression=exp.Literal(this="25", _type=exp.DataType.build("INT"), is_string = False))
# print(condition)

# context = {"row": row}
# # new_cond = condition.transform(transform, ctx=context, copy = True)


# # print(repr(new_cond))
# # print(new_cond.concrete)
# # context.pop("row")
# # print(f'context: {context}')

# from typing import Callable

# def transform_exp(expr: exp.Expression, fun: Callable, *args, copy = False, **kwargs) -> exp.Expression:
#     root = None
#     new_node = None
#     ctx = kwargs.get("ctx", {})

#     for node in (expr.copy() if copy else expr).dfs(prune=lambda n: n is not new_node):
#         parent, arg_key, index = node.parent, node.arg_key, node.index
#         if isinstance(node, exp.Predicate):
#             ctx.setdefault("smt_conditions", []).append(node)
#         if isinstance(node, exp.Column):
#             ctx.setdefault("columns", []).append(node)
#         new_node = fun(node, *args, **kwargs)
#         if not root:
#             root = new_node
#         elif new_node is not node:
#             parent.set(arg_key, new_node, index)

#     assert root
#     return root.assert_is(exp.Expression)


# newnn = transform_exp(condition, transform, ctx=context, copy = True)

# print(repr(newnn))
# context.pop("row")

# print(context["smt_conditions"])

# for smt_cond in context["smt_conditions"]:
#     sql_condition = smt_cond.transform(lambda node, ctx: ctx['mapping'][node] if node in ctx.get("mapping", {}) else node, ctx=context, copy = True)
#     print(f"SQL condition: {sql_condition}, SMT condition: {smt_cond},")

# print(f'original condition: {condition}')

# if2 = exp.If(this=exp.GT(this=exp.Column(this='age', table='users', _type=exp.DataType.build('INT')), expression=exp.Literal(this=15, _type=exp.DataType.build('INT'), is_string=False)), true=exp.Literal.number(1))

# case_when = condition = exp.Case(ifs=[exp.If(this=exp.GT(this=exp.Cast(this = exp.Column(this='age', table='users', _type=exp.DataType.build('INT')), to=exp.DataType.build('REAL')), expression=exp.Literal(this=25, _type=exp.DataType.build('INT'), is_string=False)), true=exp.Literal.number(1)), if2], default=exp.Literal.number(0))



# context = {"row": row}

# new_case = case_when.transform(transform, ctx=context, copy = True)

# # new_case = transform_exp(case_when, transform, ctx=context, copy = True)

# print(new_case)

# context.pop("row")
# print(context)

# # print(context["smt_conditions"])

# # for smt_cond in context["smt_conditions"]:
# #     print(smt_cond)


from sqlglot import parse_one

print(repr(parse_one("select * from users order by age limit 10, 10")))