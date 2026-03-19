from dataclasses import dataclass, field


# @dataclass
# class Config:
#     null_threshold: int = 1
#     unique_threshold: int = 1
#     duplicate_threshold: int = 2
#     group_count_threshold: int = 2
#     group_size_threshold: int = 3
#     positive_threshold: int = 2
#     negative_threshold: int = 1
#     max_tries: int = 5
#     set_semantic: bool = False
#     timeout: int = 360
#     seed: int = 42

#     def __post_init__(self):
#         if self.set_semantic is True:
#             self.duplicate_threshold = 0

#     def to_dict(self):
#         return {
#             "null_threshold": self.null_threshold,
#             "unique_threshold": self.unique_threshold,
#             "duplicate_threshold": self.duplicate_threshold,
#             "group_count_threshold": self.group_count_threshold,
#             "group_size_threshold": self.group_size_threshold,
#             "positive_threshold": self.positive_threshold,
#             "negative_threshold": self.negative_threshold,
#             "max_tries": self.max_tries,
#             "set_semantic": self.set_semantic,
#             "timeout": self.timeout,
#             "seed": self.seed,
#         }


@dataclass
class GeneratorConfig:
    null_threshold: int = 1
    unique_threshold: int = 1
    duplicate_threshold: int = 2
    group_count_threshold: int = 2
    group_size_threshold: int = 3
    positive_threshold: int = 2
    negative_threshold: int = 1
    max_tries: int = 5


@dataclass
class DisproverConfig:
    host_or_path: str
    db_id: str
    port: int = None
    username: str = None
    password: str = None
    query_timeout: int = 10
    global_timeout: int = 360
    set_semantic: bool = False

    generator: GeneratorConfig = field(default_factory=GeneratorConfig)


Config = GeneratorConfig
