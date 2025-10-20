import re


def normalize_name(name) -> str:
    pattern = r"[^a-zA-Z0-9_]"
    cleaned_str = re.sub(pattern, "", name)
    return cleaned_str.lower()


def like_to_pattern(pattern: str) -> re.Pattern:
    """
    Convert SQL LIKE pattern to regex pattern.
    """
    regex = ""
    for ch in pattern:
        if ch == "%":
            regex += ".*"
        elif ch == "_":
            regex += "."
        else:
            regex += re.escape(ch)
    return re.compile(f"^{regex}$")
