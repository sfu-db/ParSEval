

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)


import unittest, logging

from shutil import rmtree

from pathlib import Path


def assert_folder(file_path):
    if not Path(file_path).exists():
        Path(file_path).mkdir(parents=True, exist_ok=True)
    return file_path


def rm_folder(folder_path):
    rmtree(Path(folder_path), ignore_errors=True)


def reset_folder(folder_path):
    rm_folder(folder_path)
    assert_folder(folder_path)
from src.parseval.to_dot import display_uexpr
from src.parseval.logger import Logger
Logger(
    verbose={
        "coverage": True,
        "symbolic": False,
        "smt": True,
        "db": False,
    },
    log_file="log.log"
)

logger = logging.getLogger("parseval.coverage")

class MockUexpr:
    def advance(self, *args, **kwargs):
        return
    def which_branch(self, *args, **kwargs):
        return
class TestEncoder(unittest.TestCase):
    def test_encoder(self):
        uexpr = MockUexpr()
        display_uexpr(uexpr, "tests/encoder/uexpr.dot")
    
    
if __name__ == '__main__':
    reset_folder('tests/encoder')
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      