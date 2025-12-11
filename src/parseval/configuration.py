from dataclasses import dataclass, field


@dataclass
class Configuration:
    null_number: int = 1
    unique_number: int = 1
    duplicate_number: int = 1
    group_count: int = 1
    group_size: int = 1
    seed: int = 42
    distinct: bool = False
    positive_threshold: int = 2
    negative_threshold: int = 1
