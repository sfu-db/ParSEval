

import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)


import unittest
from sqlglot import parse_one, exp
schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT PRIMARY KEY, `Academic Year` TEXT, `County Code` TEXT, `District Code` INT, `Free Meal Count (K-12)` FLOAT, FOREIGN KEY (`CDSCode`) REFERENCES `schools`(`CDSCode`));
CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `NumGE1500` INT, PRIMARY KEY (`cds`));
CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

sql = """SELECT T2.NCESDist FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode"""


class TestQuery(unittest.TestCase):
    @unittest.skip("skipping table creation")
    def test_table(self):
        from src.instance.table import Table
        ddl = """CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

        print(repr(parse_one(ddl, dialect='sqlite')))

        table = Table.create(parse_one(ddl, dialect='sqlite'))
        print(table)
        print(repr(table))

    def test_concrete_generator(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        int_generator = ValueGeneratorRegistry.get_generator('int')
        ints = set()
        for _ in range(100):
            value = int_generator(is_unique=True, existing_values=ints)
            ints.add(value)

        self.assertEqual(len(ints), 100)

    # @unittest.skip("skipping table creation")
    def test_create_row(self):
        from src.instance.instance import Instance
        instance = Instance.create(schema= schema, dialect= 'sqlite')
        instance._create_row_internal('schools', values= {})
        instance._create_row_internal('schools', values= {})
        instance._create_row_internal('schools', values= {})
        instance._create_row_internal('schools', values= {})
        for row in instance.get_table('schools'):
            print(row)

            # for col in row:
            #     print(col)

    @unittest.skip("skipping table creation")
    def test_create_row_with_fk(self):
        from src.instance.instance import Instance
        instance = Instance.create(schema= schema, dialect= 'sqlite')

        instance.create_row('frpm', values= {})

        for row in instance.get_table('frpm'):
            for col in row:
                print(repr(col))
        print('-'*100)
        for row in instance.get_table('schools'):
            for col in row:
                print(repr(col))
    @unittest.skip("skipping table creation")
    def test_db_constraints(self):
        from src.instance.instance import Instance
        instance = Instance.create(schema= schema, dialect= 'sqlite')


        for i in range(3):
            instance.create_row('frpm', values= {})

        self.assertEqual(instance.get_table('frpm').shape[0], 3)
        self.assertEqual(instance.get_table('schools').shape[0], 3)
        for fk in instance._get_foreign_key_constraints():
            print(repr(fk))
        
        for sc in instance._get_size_constraints():
            print(sc)
    def test_to_db(self):
        from src.instance.instance import Instance
        instance = Instance.create(schema= schema, dialect= 'sqlite', name = 'test2.sqlite')
        for i in range(3):
            instance.create_row('frpm', values= {})

        table = instance.get_table('schools')

        for row in table:
            row.multiplicity.set('value', 3)

        # print(instance.to_insert())

        instance.to_db(host_or_path = 'tests/db')

if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)