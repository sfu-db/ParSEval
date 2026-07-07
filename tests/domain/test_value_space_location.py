from parseval.domain.value_space import ValueSpace
from parseval.dtype import TypeFamily


def test_value_space_is_owned_by_domain_package():
    space = ValueSpace(family=TypeFamily.INTEGER)

    space.narrow_min(3)
    space.narrow_max(5)

    assert 3 <= space.pick() <= 5
