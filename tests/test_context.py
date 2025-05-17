
import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
from src.corekit import get_ctx, reset_folder, rm_folder
import logging, time
import pandas as pd

logger = logging.getLogger('src.test.naive')
from src.context import Context
# from src.instance import Instance, create_instance
from src.instance.instance2 import create_instance, Instance

schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT, `County Code` TEXT, `District Code` INT, `School Code` TEXT, `County Name` TEXT, `District Name` TEXT, `School Name` TEXT, `District Type` TEXT, `School Type` TEXT, `Educational Option Type` TEXT, `NSLP Provision Status` TEXT, `Charter School (Y/N)` INT, `Charter School Number` TEXT, `Charter Funding Type` TEXT, `IRC` INT, `Low Grade` TEXT, `High Grade` TEXT, `Enrollment (K-12)` FLOAT, `Free Meal Count (K-12)` FLOAT, `Percent (%) Eligible Free (K-12)` FLOAT, `FRPM Count (K-12)` FLOAT, `Percent (%) Eligible FRPM (K-12)` FLOAT, `Enrollment (Ages 5-17)` FLOAT, `Free Meal Count (Ages 5-17)` FLOAT, `Percent (%) Eligible Free (Ages 5-17)` FLOAT, `FRPM Count (Ages 5-17)` FLOAT, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT, `2013-14 CALPADS Fall 1 Certification Status` INT, PRIMARY KEY (`CDSCode`), FOREIGN KEY (`CDSCode`) REFERENCES schools (`CDSCode`));CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `enroll12` INT, `NumTstTakr` INT, `AvgScrRead` INT, `AvgScrMath` INT, `AvgScrWrite` INT, `NumGE1500` INT, PRIMARY KEY (`cds`), FOREIGN KEY (`cds`) REFERENCES schools (`CDSCode`));CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, `County` TEXT, `District` TEXT, `School` TEXT, `Street` TEXT, `StreetAbr` TEXT, `City` TEXT, `Zip` TEXT, `State` TEXT, `MailStreet` TEXT, `MailStrAbr` TEXT, `MailCity` TEXT, `MailZip` TEXT, `MailState` TEXT, `Phone` TEXT, `Ext` TEXT, `Website` TEXT, `OpenDate` DATE, `ClosedDate` DATE, `Charter` INT, `CharterNum` TEXT, `FundingType` TEXT, `DOC` TEXT, `DOCType` TEXT, `SOC` TEXT, `SOCType` TEXT, `EdOpsCode` TEXT, `EdOpsName` TEXT, `EILCode` TEXT, `EILName` TEXT, `GSoffered` TEXT, `GSserved` TEXT, `Virtual` TEXT, `Magnet` INT, `Latitude` FLOAT, `Longitude` FLOAT, `AdmFName1` TEXT, `AdmLName1` TEXT, `AdmEmail1` TEXT, `AdmFName2` TEXT, `AdmLName2` TEXT, `AdmEmail2` TEXT, `AdmFName3` TEXT, `AdmLName3` TEXT, `AdmEmail3` TEXT, `LastUpdate` DATE, PRIMARY KEY (`CDSCode`))
"""

def test_instance(workspace, size, ctx, schema):
    instance = create_instance(context= ctx, schema= schema, size= size, initial_values= {},  name = 'test_instance')
    print(instance._tables.keys())
    instance.to_db(workspace, f'{instance.name}_{size}_.sqlite')
    # print(instance.get_db_constraints())

    # print(ctx.get('tuple_id_to_symbols'))


def test_symbol_bool(ctx):
    from src.expr.symbol import Boolean, Symbol, Integer, String, create_symbol, sany, sall, get_all_symbols, substitute
    a = create_symbol('bool', ctx, 'name', None)    
    boolb = create_symbol('bool', ctx, 'name1', True)
    boolc = create_symbol('bool', ctx, 'name2', False)
    boold = create_symbol('bool', ctx, 'name3', None)

   

    intb = create_symbol('int', ctx, 'name1', 257)
    intc = create_symbol('int', ctx, 'name2', 256)
    intd = create_symbol('int', ctx, 'name3', 28885)
    inte = create_symbol('int', ctx, 'name4', 2)

    # d = intb * intc + intd - intc * intd > 200

    # from src.expr.utils import get_all_symbols
    # print(d)

    # print(get_all_symbols(d))

    # print( repr(intb > 10))
    c = intb + intc
    print(str(c))
    print(repr(c))
    print(repr(intb / intc))
    
    print(repr(intb > 100))
    dd = intb > intc
    print(type(dd))
    print(repr(dd))
    ff = dd.and_(intd > 100)
    ee = ff.and_(intd >= 2200)
    for a in ee.expr.flatten():
        print(a.key)
    # print(ee.expr.flatten())
    print(repr(ee))


    if sall(intc < 100, sany(c > 0, intb > 50)):
        ...

    print(dd.__not__())

    eee = dd.__not__()

    print(eee.__not__().__not__().__not__().__not__())

    print(get_all_symbols(ee))

    print('**' * 10)
    print(substitute(ff, intd, inte))

    print('**' * 10)
    print('dfs')
    for v in ee.dfs():
        print(v, type(v))


def test_extend_symbol(ctx):
    from src.expr.symbol import Boolean, Symbol, Integer, String, create_symbol, sany, sall, get_all_symbols, substitute, extend_summation
    
    age1 = create_symbol('str', ctx, 'age1', 15)
    age2 = create_symbol('str', ctx, 'age2', 15)
    age3 = create_symbol('str', ctx, 'age3', 18)
    age4 = create_symbol('str', ctx, 'age4', 18)


    expr1 = age1 == age2
    import copy

    age1_copy = copy.deepcopy(age1)
    print(age1_copy is age1)

    print(repr(age1 == 25))

    print(expr1, type(expr1))

    # print(extend_summation(expr1, age1, age3))



def test_exprs(ctx):
    from src.expr.exprs import to_variable
    variable_name = to_variable('text', ctx, name= 'name', value= "hello")
    variable_age = to_variable('int', ctx, name= "age", value = 15)

    print( variable_age > 25)
    print( variable_age >= 25)
    print(variable_age < 20)
    print(variable_age <= 20)

    print(variable_age == 20)
    print(variable_age != 20)

    print(variable_age == None)


    print(variable_age + 40)
    print(variable_age - 40)
    print(variable_age * 40)
    print(variable_age / 40)


    condition1 = variable_age > 25
    condition2 = variable_age < 50

    cond3 = condition1.and_(condition2)
    cond4 = cond3.and_(variable_age / 50 > 25)
    print(repr(cond4))
    # cond4.print_tree()


    # print(cond4))
    # print(condition2.value)

    # for e in condition2.iter_expressions():
    #     print(e, "===")

    # print(condition1.and_(condition2))

def test_dtype():
    from src.expression.types import DataType

    typ1 = DataType.build('Text')
    print(typ1)


if __name__ == '__main__':

    get_ctx(log_level = 'INFO')
    workspace = 'tests/db'
    reset_folder(workspace)
    ctx = Context()
    # test_symbol_bool(ctx)
    # test_instance(workspace, 5, ctx, schema)
    # test_exprs(ctx)
    test_dtype()
    # # test_instance(workspace)
    # print(ctx)
    # print(ctx.get('paths'))
    rm_folder(get_ctx().result_path)



