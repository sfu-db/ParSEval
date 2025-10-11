from __future__ import annotations


def clean_identifier(name: str) -> str:
    """Cleans SQL identifiers by removing quotes and converting to lowercase."""
    if not name:
        return name
    if (name.startswith('"') and name.endswith('"')) or (
        name.startswith("'") and name.endswith("'")
    ):
        name = name[1:-1]
    return name.lower()
