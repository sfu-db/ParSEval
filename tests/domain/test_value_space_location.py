import subprocess
import sys

from parseval.domain.value_space import ValueSpace
from parseval.dtype import TypeFamily


def test_value_space_is_owned_by_domain_package():
    space = ValueSpace(family=TypeFamily.INTEGER)

    space.narrow_min(3)
    space.narrow_max(5)

    assert 3 <= space.pick() <= 5


def test_importing_value_space_does_not_load_database_builder():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import parseval.domain.value_space; "
            "assert 'parseval.domain.builder' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
