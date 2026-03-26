from pathlib import Path
from itertools import count


def unique_path(p: str | Path, create=False):
    """
    Return a unique path by appending a numeric suffix if the path exists.
    Examples:
    result.json      -> result_1.json
    outputs/         -> outputs_1/
    model.v1/        -> model.v1_1/

    Args:
        path: Desired file or directory path.

    Returns:
        A unique Path object.
    """
    path = Path(p)

    if not path.exists():
        return path

    parent = path.parent
    suffix = path.suffix

    if suffix:
        stem = path.stem
        for i in count(1):
            candidate = parent / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                return candidate
    else:
        # this is a directory, we can just append _1, _2, etc. to the directory name
        name = path.name
        for i in count(1):
            candidate = parent / f"{name}_{i}"
            if not candidate.exists():
                if create:
                    candidate.mkdir(parents=True, exist_ok=True)
                return candidate
    raise RuntimeError("This should never happen")
