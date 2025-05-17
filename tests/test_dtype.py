import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)


def test_dtype():
    from src.expression.types import DataType

    typ1 = DataType.build('Text')
    print(typ1)

    print(typ1.is_type(*DataType.TEXT_TYPES))
test_dtype()