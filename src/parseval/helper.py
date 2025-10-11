import re


def normalize_name(name) -> str:
    pattern = r"[^a-zA-Z0-9_]"
    cleaned_str = re.sub(pattern, "", name)
    return cleaned_str.lower()
