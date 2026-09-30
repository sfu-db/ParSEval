"""String theory operations with explicit ASCII case and ISO date domains.

LIKE has no length bound. Case conversion is ASCII (no locale is represented
by the IR); date casts admit canonical AD ISO dates in Python's date range.
The caller guards domain constraints for NULL arguments.
"""
from functools import lru_cache

import z3


@lru_cache(maxsize=1)
def _recursive_functions():
    string = z3.StringSort()
    value, pattern = z3.Strings('pv_text_value pv_text_pattern')
    lower = z3.RecFunction('pv_ascii_lower', string, string)
    head = z3.SubString(value, 0, 1)
    tail = z3.SubString(value, 1, z3.Length(value) - 1)
    code = z3.StrToCode(head)
    lowered = z3.If(z3.And(code >= 65, code <= 90), z3.StrFromCode(code + 32), head)
    z3.RecAddDefinition(lower, (value,), z3.If(value == '', z3.StringVal(''), z3.Concat(lowered, lower(tail))))

    like = z3.RecFunction('pv_dynamic_like', string, string, z3.BoolSort())
    ph = z3.SubString(pattern, 0, 1)
    pt = z3.SubString(pattern, 1, z3.Length(pattern) - 1)
    escaped = z3.SubString(pattern, 1, 1)
    rest = z3.SubString(pattern, 2, z3.Length(pattern) - 2)
    z3.RecAddDefinition(like, (value, pattern), z3.If(
        pattern == '', value == '',
        z3.If(ph == '%', z3.Or(like(value, pt), z3.If(value != '', like(tail, pattern), False)),
              z3.If(ph == '\\', z3.If(z3.And(z3.Length(pattern) >= 2, value != '', head == escaped), like(tail, rest), False),
                    z3.If(z3.And(value != '', z3.Or(ph == '_', head == ph)), like(tail, pt), False))),
    ))
    # A final unescaped backslash is an SQL error, not a literal or wildcard.
    valid = z3.RecFunction('pv_valid_like_pattern', string, z3.BoolSort())
    z3.RecAddDefinition(valid, (pattern,), z3.If(pattern == '', True,
        z3.If(ph == '\\', z3.If(z3.Length(pattern) >= 2, valid(rest), False), valid(pt))))
    return lower, like, valid


def ascii_domain(value):
    return z3.InRe(value, z3.Star(z3.Range('\x00', '\x7f')))


def ascii_lower(value):
    return _recursive_functions()[0](value)


def dynamic_like(value, pattern):
    return _recursive_functions()[1](value, pattern)


def valid_like_pattern(pattern):
    return _recursive_functions()[2](pattern)


def iso_date(value):
    """Return (ordinal, valid) for canonical YYYY-MM-DD, including leap years."""
    digit = z3.Range('0', '9')
    syntax = z3.Concat(z3.Loop(digit, 4, 4), z3.Re('-'), z3.Loop(digit, 2, 2), z3.Re('-'), z3.Loop(digit, 2, 2))
    year = z3.StrToInt(z3.SubString(value, 0, 4))
    month = z3.StrToInt(z3.SubString(value, 5, 2))
    day = z3.StrToInt(z3.SubString(value, 8, 2))
    leap = z3.And(year % 4 == 0, z3.Or(year % 100 != 0, year % 400 == 0))
    days = z3.If(month == 2, z3.If(leap, 29, 28),
                 z3.If(z3.Or(month == 4, month == 6, month == 9, month == 11), 30, 31))
    valid = z3.And(z3.InRe(value, syntax), year >= 1, year <= 9999,
                   month >= 1, month <= 12, day >= 1, day <= days)
    before = z3.IntVal(0)
    for m, offset in enumerate((0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334), 1):
        before = z3.If(month == m, offset, before)
    previous = year - 1
    ordinal = 365 * previous + previous / 4 - previous / 100 + previous / 400 + before + day + z3.If(z3.And(leap, month > 2), 1, 0)
    return ordinal, valid
