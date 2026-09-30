"""Convert LeetCode JSON schemas into MySQL DDL for catalog tests.

The benchmark represents column references as ``table__column`` and keeps
keys and row-local checks in a separate JSON tree. Cross-table checks and
other non-DDL assumptions are intentionally ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def mysql_schema_to_ddl(
    schema: Mapping[str, Mapping[str, str]],
    constraints: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Convert the LeetCode JSON schema format into MySQL DDL."""

    normalized = {
        _identifier(table): {
            _identifier(column): data_type
            for column, data_type in columns.items()
        }
        for table, columns in schema.items()
    }
    primary_keys: dict[str, list[list[str]]] = {}
    foreign_keys: list[tuple[str, str, str, str]] = []
    checks: dict[str, list[str]] = {}

    for constraint in constraints or ():
        if "primary" in constraint:
            references = [_reference(item["value"]) for item in constraint["primary"]]
            if references:
                primary_keys.setdefault(references[0][0], []).append(
                    [column for _, column in references]
                )
        elif "foreign" in constraint:
            source, target = constraint["foreign"]
            source_table, source_column = _reference(source["value"])
            target_table, target_column = _reference(target["value"])
            foreign_keys.append(
                (source_table, source_column, target_table, target_column)
            )
        else:
            check = _row_local_check(constraint)
            if check is not None:
                table, expression = check
                checks.setdefault(table, []).append(expression)

    dependencies = {table: set() for table in normalized}
    referenced: dict[str, set[str]] = {}
    for source, _, target, target_column in foreign_keys:
        if source != target and source in dependencies and target in dependencies:
            dependencies[source].add(target)
        referenced.setdefault(target, set()).add(target_column)

    statements: list[str] = []
    created: set[str] = set()
    for table in _topological_order(dependencies):
        definitions = [
            f"{column} {_data_type(data_type)}"
            for column, data_type in normalized[table].items()
        ]
        keys = primary_keys.get(table, [])
        primary = set(keys[0]) if keys else set()
        if keys:
            definitions.append(f"PRIMARY KEY ({', '.join(keys[0])})")
        seen = {tuple(keys[0])} if keys else set()
        for columns in keys[1:]:
            if tuple(columns) not in seen:
                definitions.append(f"UNIQUE ({', '.join(columns)})")
                seen.add(tuple(columns))
        for column in referenced.get(table, set()):
            if column not in primary and [column] not in keys:
                definitions.append(f"INDEX ({column})")
        for source, source_column, target, target_column in foreign_keys:
            if source == table and (source == target or target in created):
                definitions.append(
                    f"FOREIGN KEY ({source_column}) "
                    f"REFERENCES {target}({target_column})"
                )
        definitions.extend(f"CHECK ({item})" for item in checks.get(table, ()))
        statements.append(f"CREATE TABLE {table} ({', '.join(definitions)})")
        created.add(table)
    return "; ".join(statements)


def _identifier(value: str) -> str:
    return value.lower()


def _reference(value: str) -> tuple[str, str]:
    table, column = value.split("__", 1)
    return _identifier(table), _identifier(column)


def _references(node: Any) -> set[tuple[str, str]]:
    if isinstance(node, dict):
        if set(node) == {"value"}:
            return {_reference(node["value"])}
        return set().union(*(_references(value) for value in node.values()), set())
    if isinstance(node, list):
        return set().union(*(_references(value) for value in node), set())
    return set()


def _literal(value: Any) -> str:
    if isinstance(value, dict) and set(value) in ({"date"}, {"literal"}):
        return _literal(next(iter(value.values())))
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if value is None:
        return "NULL"
    return str(value)


def _operand(value: Any, table: str) -> str:
    if isinstance(value, dict) and set(value) == {"value"}:
        source, column = _reference(value["value"])
        if source != table:
            raise ValueError("cross-table operand")
        return column
    return _literal(value)


def _row_local_check(constraint: Mapping[str, Any]) -> tuple[str, str] | None:
    if len(constraint) != 1:
        return None
    operator, values = next(iter(constraint.items()))
    if operator in {"primary", "foreign", "inc", "consec"}:
        return None
    tables = {table for table, _ in _references(values)}
    if len(tables) != 1:
        return None
    table = next(iter(tables))
    try:
        expression = _check_expression(operator, values, table)
    except ValueError:
        return None
    return None if expression is None else (table, expression)


def _check_expression(operator: str, values: Any, table: str) -> str | None:
    comparisons = {
        "eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
    }
    if operator in comparisons:
        left, right = values
        return f"{_operand(left, table)} {comparisons[operator]} {_operand(right, table)}"
    if operator == "between":
        column, low, high = values
        return (
            f"{_operand(column, table)} BETWEEN {_operand(low, table)} "
            f"AND {_operand(high, table)}"
        )
    if operator == "in":
        column, *choices = values
        if len(choices) == 1 and isinstance(choices[0], list):
            choices = choices[0]
        rendered = ", ".join(_operand(choice, table) for choice in choices)
        return f"{_operand(column, table)} IN ({rendered})"
    if operator == "imply":
        antecedent, consequent = values
        left = _nested_check(antecedent, table)
        right = _nested_check(consequent, table)
        return None if left is None or right is None else f"(NOT ({left}) OR ({right}))"
    return None


def _nested_check(node: Any, table: str) -> str | None:
    if not isinstance(node, dict) or len(node) != 1:
        return None
    return _check_expression(*next(iter(node.items())), table)


def _data_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized.startswith("ENUM,"):
        choices = (
            "'" + item.strip().replace("'", "''") + "'"
            for item in value.strip().split(",")[1:]
        )
        return f"ENUM({','.join(choices)})"
    if normalized in {"VARCHAR", "CHAR"}:
        return f"{normalized}(255)"
    return value


def _topological_order(dependencies: Mapping[str, set[str]]) -> list[str]:
    degree = {table: 0 for table in dependencies}
    children = {table: [] for table in dependencies}
    for table, parents in dependencies.items():
        for parent in parents:
            if parent != table:
                children[parent].append(table)
                degree[table] += 1
    queue = [table for table in dependencies if degree[table] == 0]
    result: list[str] = []
    while queue:
        table = queue.pop(0)
        result.append(table)
        for child in children[table]:
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    result.extend(table for table in dependencies if table not in result)
    return result


__all__ = ["mysql_schema_to_ddl"]
