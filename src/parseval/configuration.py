from dataclasses import dataclass, field


@dataclass
class Configuration:
    null_rate: float = 0.0
    duplicate_rate: float = 0.0
    group_count: int = 1
    group_size: int = 1
    seed: int = 42
    distinct: bool = False
    positive_threshold: float = 0.5
    negative_threshold: float = 0.5
