from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlglot import exp, parse

from .enums import DBLevel, QueryLevel

try:
    from parseval.configuration import DisproverConfig, GeneratorConfig
    from parseval.states import ExecutionResult, RunResult
except ModuleNotFoundError:
    from src.parseval.configuration import DisproverConfig, GeneratorConfig
    from src.parseval.states import ExecutionResult, RunResult


def process_ddl_by_db_level(
    ddls: dict[str, Any] | list[str] | str | None, dialect: str, db_level: str
):
    db_level = DBLevel(db_level.upper()) if isinstance(db_level, str) else db_level
    if ddls is None or db_level == DBLevel.FULL:
        return _schema_to_ddl_text(ddls, dialect) if ddls is not None else None

    ddl_text = _schema_to_ddl_text(ddls, dialect)
    preprocessed_ddls: list[str] = []
    exprs = parse(ddl_text, read=dialect)

    def transform(node):
        if db_level == DBLevel.NONE:
            if isinstance(node, exp.ColumnConstraint):
                return None
            if isinstance(node, (exp.PrimaryKey, exp.ForeignKey)):
                return None
        if db_level == DBLevel.PK_FK:
            if isinstance(node, exp.ColumnConstraint) and not isinstance(
                node.kind, exp.PrimaryKeyColumnConstraint
            ):
                return None
        if db_level == DBLevel.PK_FK_NULL:
            if isinstance(node, exp.ColumnConstraint) and not isinstance(
                node.kind, (exp.PrimaryKeyColumnConstraint, exp.NotNullColumnConstraint)
            ):
                return None
        return node

    for expr in exprs:
        transformed_expr = expr.transform(transform)
        preprocessed_ddls.append(transformed_expr.sql(dialect=dialect))
    return ";".join(preprocessed_ddls)


def build_parseval_config(
    db_level: str,
    query_level: str,
    project_settings: dict[str, Any],
    *,
    host_or_path: str,
    db_id: str,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
) -> DisproverConfig:
    db_level = DBLevel(db_level.upper()) if isinstance(db_level, str) else db_level
    query_level = (
        QueryLevel(query_level.upper()) if isinstance(query_level, str) else query_level
    )
    null_threshold = _coerce_int(project_settings.get("null_threshold", 1), 1)
    unique_threshold = _coerce_int(project_settings.get("unique_threshold", 1), 1)
    duplicate_threshold = _coerce_int(project_settings.get("duplicate_threshold", 2), 2)
    if db_level in {DBLevel.PK_FK, DBLevel.PK_FK_NULL}:
        null_threshold = 0
        unique_threshold = 0

    if query_level == QueryLevel.SET:
        duplicate_threshold = 0

    generator_config = GeneratorConfig(
        null_threshold=null_threshold,
        unique_threshold=unique_threshold,
        duplicate_threshold=duplicate_threshold,
        group_count_threshold=_coerce_int(
            project_settings.get("group_count_threshold"), 2
        ),
        group_size_threshold=_coerce_int(
            project_settings.get("group_size_threshold"), 3
        ),
        positive_threshold=_coerce_int(project_settings.get("positive_threshold"), 2),
        negative_threshold=_coerce_int(project_settings.get("negative_threshold"), 1),
        max_tries=_coerce_int(project_settings.get("max_tries"), 5),
    )

    # QueryLevel controls how result sets are compared for this evaluation level.
    set_semantic = query_level == QueryLevel.SET
    if set_semantic:
        generator_config.duplicate_threshold = 0

    return DisproverConfig(
        host_or_path=host_or_path,
        db_id=db_id,
        port=port,
        username=username,
        password=password,
        query_timeout=_coerce_int(project_settings.get("query_timeout"), 10),
        global_timeout=_coerce_int(project_settings.get("global_timeout"), 360),
        set_semantic=set_semantic,
        generator=generator_config,
    )


def disprove_queries(
    *,
    dataset: str,
    q1: str,
    q2: str,
    schema: str | None,
    dialect: str,
    db_level: str,
    query_level: str,
    project_settings: dict[str, Any],
    host_or_path: str,
    db_id: str,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
) -> RunResult:
    existing_dbs = []
    if dialect.lower() == "sqlite":
        from pathlib import Path

        host_or_path = Path(host_or_path) / dataset / db_id / db_level / query_level
        host_or_path.mkdir(parents=True, exist_ok=True)
        host_or_path = str(host_or_path)

        def find_sqlite_files(root_dir):
            return list(Path(root_dir).rglob("*.sqlite"))

        files = find_sqlite_files(host_or_path)
        for f in files:
            existing_dbs.append((str(f.parent), f.name, None, None, None))

    config = build_parseval_config(
        db_level,
        query_level,
        project_settings,
        host_or_path=host_or_path,
        db_id=db_id,
        port=port,
        username=username,
        password=password,
    )

    disprover_cls = _load_disprover()
    disprover = disprover_cls(
        q1=q1,
        q2=q2,
        schema=process_ddl_by_db_level(schema, dialect, db_level),
        dialect=dialect,
        config=config,
        exisiting_dbs=existing_dbs,
    )
    return disprover.run()


def execution_result_to_payload(
    result: ExecutionResult | None,
    *,
    query_timeout: int,
) -> dict[str, Any] | None:
    if result is None:
        return None

    payload = {
        "query": result.query,
        "host_or_path": result.host_or_path,
        "db_id": result.db_id,
        "dialect": result.dialect,
        "elapsed_time": result.elapsed_time,
        "rows": [list(row) for row in result.rows],
        "columns": [],
        "error_msg": result.error_msg,
    }
    if result.error_msg:
        return payload

    db_manager_cls = _load_db_manager()
    with db_manager_cls().get_connection(
        host_or_path=result.host_or_path,
        database=f"{result.db_id}",
        dialect=result.dialect,
    ) as conn:
        records = conn.execute(
            result.query,
            fetch="all",
            timeout=query_timeout,
        )
    if records:
        payload["columns"] = []
        payload["rows"] = [list(row) for row in records[1:]]
    return payload


def witness_db_to_payload(
    *,
    host_or_path: str,
    db_id: str,
    dialect: str,
) -> dict[str, Any]:
    db_manager_cls = _load_db_manager()
    with db_manager_cls().get_connection(
        host_or_path=host_or_path,
        database=db_id,
        dialect=dialect,
    ) as conn:
        tables = []
        for table_name, records in conn.get_all_table_rows().items():
            columns = list(records[0]) if records else []
            rows = [list(row) for row in records[1:]] if records else []
            tables.append(
                {
                    "name": table_name,
                    "columns": columns,
                    "rows": rows,
                }
            )
    return {
        "db_id": db_id,
        "host_or_path": host_or_path,
        "database": db_id,
        "tables": tables,
    }


def sqlite_generation_root(host_or_path: str) -> str:
    path = Path(host_or_path)
    if path.suffix:
        return str(path.parent)
    return str(path)


def _schema_to_ddl_text(
    ddls: dict[str, Any] | list[str] | str | None,
    dialect: str,
) -> str:
    if ddls is None:
        return ""
    if isinstance(ddls, str):
        return ddls
    if isinstance(ddls, list):
        return ";".join(ddls)

    statements = []
    for table_name, column_defs in ddls.items():
        columns = [
            exp.ColumnDef(
                this=exp.to_identifier(column_name, quoted=True),
                kind=exp.DataType.build(column_type),
            )
            for column_name, column_type in column_defs.items()
        ]
        statements.append(
            exp.Create(
                this=exp.Schema(
                    this=exp.to_identifier(table_name, quoted=True),
                    expressions=columns,
                ),
                exists=True,
                kind="TABLE",
            ).sql(dialect=dialect)
        )
    return ";".join(statements)


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _load_db_manager():
    try:
        from parseval.db_manager import DBManager
    except ModuleNotFoundError:
        from src.parseval.db_manager import DBManager
    return DBManager


def _load_disprover():
    try:
        from parseval.disprover import Disprover
    except ModuleNotFoundError:
        from src.parseval.disprover import Disprover
    return Disprover
