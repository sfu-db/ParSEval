"""Persistence helpers for :class:`parseval.instance.Instance`.

Extracted from ``Instance.to_db`` so the Instance class itself stays
focused on in-memory row management. Callers that need to write an
Instance to a live database or render SQL fixtures import from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from .exporter import InstanceExporter
from .loader import InstanceLoader
from .serialization import InstanceValueSerializer
from .types import DatabaseTarget

if TYPE_CHECKING:
    from .core import Instance


def to_db(
    instance: "Instance",
    connection_string: str,
    dialect: Optional[str] = None,
    truncate_first: bool = True,
    return_inserted: bool = False,
) -> Union[str, None]:
    """Write ``instance``'s current rows to a live database.

    This is the functional equivalent of the old ``Instance.to_db``
    method, extracted so the Instance class doesn't carry persistence
    concerns. The Instance method still exists as a thin delegation for
    backward compatibility.

    Parameters
    ----------
    instance : Instance
        The in-memory instance to persist.
    connection_string : str
        SQLAlchemy-style connection string (e.g. ``sqlite:///path``).
    dialect : str, optional
        SQL dialect for the target. Defaults to ``instance.dialect``.
    truncate_first : bool
        Whether to drop existing tables before inserting.
    return_inserted : bool
        If True, return the rendered INSERT SQL instead of the write result.
    """
    dialect = dialect or instance.dialect
    snapshot = instance.snapshot()
    target = DatabaseTarget(
        connection_string=connection_string,
        dialect=dialect,
    )
    serializer = InstanceValueSerializer(instance.schema_spec)
    result = InstanceLoader().load(
        snapshot=snapshot,
        target=target,
        serializer=serializer,
        truncate_first=truncate_first,
    )
    if return_inserted:
        return "\n".join(
            InstanceExporter().render_sql(
                snapshot,
                serializer=serializer,
                dialect=target.dialect,
            )
        )
    return result


__all__ = ["to_db"]
