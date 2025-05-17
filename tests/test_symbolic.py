

import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
import random, logging


from src.symbols.ssa_factory import ssa_factory

def test_symbolic():
    print('supported symbolic types')
    print(ssa_factory.get_supported_symbolic_types())

def test_create_dtypes():
    print(ssa_factory.create_symbol('int', 'age', 35))
    print(ssa_factory.create_symbol('str', 'name', 'eve'))
    print(ssa_factory.create_symbol('float', 'salary', 35.5))
    print(ssa_factory.create_symbol('bool', 'gender', True))

    print(ssa_factory.create_symbol('date', 'dob', '2022'))
    print(ssa_factory.create_symbol('datetime', 'gender', '2024'))


# from src.symbols.concolic_types import concolic_int, concolic_iter, concolic_list, concolic_map, concolic_object, concolic_str, concolic_type

# from symbols.base import *
import z3

# def test_concolic_types():
#     a = concolic_int.ConcolicInteger('age1', 20)

#     b = a + 50

#     age_large20 = a.compare_op('>', concolic_int.ConcolicInteger('t', 25))

#     print(age_large20)
#     print(b)

#     print(age_large20.to_formula())


def test_z3():
    a = z3.String('a')
    b = z3.String('b')

    b = z3.StringVal('155')

    c = z3.Int('c')

    d =  c > 160
    print(d)
    print(a > b)

    solver = z3.Solver()
    solver.add(a > b)

    solver.add(d)

    # solver.add( )
    solver.add(z3.Length(a) > z3.Length(b))
    solver.add(z3.Length(b) > 1)

    if solver.check() == z3.sat:
        print(solver.model())

# test_concolic_types()

def test_symbolic_numberic_types():
    context= ({},{})
    si = ssa_factory.create_symbol('int', context, 'age', 5)
    
    print(si + 5)
    print(si - 5)
    print(si * 5)
    print(si / 5)
    real_symbol_1 = ssa_factory.create_symbol('float', context, 'salary', 10.8)
    print(real_symbol_1 + 0.5)
    print(real_symbol_1 - 0.5)
    print(real_symbol_1 * 0.5)
    print(real_symbol_1 / 0.5)
    print(si + real_symbol_1)
    print(si - real_symbol_1)
    print(si * real_symbol_1)
    print(si / real_symbol_1)

def test_symbolic_bool_ops():
    context= ({}, [])

    bool_val = ssa_factory.create_symbol('bool', context, 'ba1', True)
    bool_val2 = ssa_factory.create_symbol('bool', context, 'ba2', True)

    bll_add = bool_val + bool_val2

    print(repr(bll_add))
    print(True + False)


    si = ssa_factory.create_symbol('int', context, 'age', 5)

    print(si == 5)
    print(si != 5)
    print(si> 5)
    print(si >= 5)
    print(si <= 5)
    print(si < 5)

    bbb = si < 5

    print(bbb.__not__().logical(si > 5, 'and'))
    print('----')

    string_sym_1 = ssa_factory.create_symbol('string', context, 'name', '-25')

    print('al' in string_sym_1)

    print(string_sym_1.to_int())

    solver = z3.Solver()

    # tmp = string_sym_1.to_int() < -50

    tmp = string_sym_1.startswith('al')

    tmp2 = string_sym_1.endswith('ice')

    # print(repr(tmp))
    # print(tmp.expr)

    solver.add(tmp.expr)
    solver.add(tmp2.expr)

    print(solver.check())
    print(solver.model())
    
def test_symbolic_datetime_types():
    from datetime import datetime
    context= ({},[])
    si = ssa_factory.create_symbol('datetime', context, 'dob', 202005)

    age_sym = ssa_factory.create_symbol('int', context, 'age', 25)
    year = si.strftime('%Y')

    left = year > 2024
    right = age_sym > 20

    if left.connector(right, 'and'):
        print('------')
        ...

    print(year > 2024)
    constraint = year > 2024
    solver = z3.Solver()
    solver.add(constraint.expr)
    print(solver.check())
    print(solver.model())
    res = solver.model().evaluate(si.expr).as_long()
    print(datetime.fromtimestamp(res))

    print(context)


test_symbolic_bool_ops()