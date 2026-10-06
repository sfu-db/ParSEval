"""PostgreSQL differential replay in a scratch database (parseval.db_manager)."""
from decimal import Decimal
from math import isclose
from parseval.db_manager import DBManager

from .execution import result


def _value(value, type_oid=None):
    # PostgreSQL bpchar (OID 1042) pads CHAR(n) results. Compare its logical
    # value; TEXT/VARCHAR trailing spaces remain significant.
    if type_oid == 1042 and isinstance(value, str):
        return value.rstrip(' ')
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _row_matches(left, right):
    """The core uses approximate numeric evaluation; structure stays exact."""
    if len(left) != len(right):
        return False
    numeric = (int, float, Decimal)
    return all(isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
               if isinstance(a, numeric) and isinstance(b, numeric)
               and not isinstance(a, bool) and not isinstance(b, bool)
               and (isinstance(a, (float, Decimal)) or isinstance(b, (float, Decimal)))
               else a == b for a, b in zip(left, right, strict=True))


def _matches(actual, expected):
    """Results as bags: rows that tie on ORDER BY keys may come in any order."""
    if len(actual) != len(expected):
        return False
    # Bipartite matching preserves multiplicity even when tolerance creates
    # overlapping candidate matches. Greedy deletion can reject a valid match.
    matched = {}
    def augment(index, seen):
        for other, row in enumerate(actual):
            if other in seen or not _row_matches(expected[index], row):
                continue
            seen.add(other)
            if other not in matched or augment(matched[other], seen):
                matched[other] = index
                return True
        return False
    return all(augment(index, set()) for index in range(len(expected)))


def validate_corpus(query, ddl, catalog, instances, dsn):
    """Load each instance into a scratch PostgreSQL database and compare results."""
    database = DBManager(dsn, 'postgres')
    checked = 0
    for instance in instances:
        with database.scratch() as connection:
            connection.create_tables(ddl)
            connection.load(instance)
            types, *rows = connection.execute(query, with_column_types=True, timeout=5)
        actual = [tuple(_value(value, oid) for value, oid in zip(row, types, strict=True)) for row in rows]
        rows, _ = result(query, catalog, instance)
        expected = [tuple(_value(value, oid) for value, oid in zip(row, types, strict=True)) for row in rows]
        if not _matches(actual, expected):
            raise AssertionError(f'PostgreSQL replay mismatch: expected {expected[:5]!r}, actual {actual[:5]!r}')
        checked += 1
    return {'instances_checked': checked, 'matched': True}
