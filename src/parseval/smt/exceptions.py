from __future__ import annotations


class InConsistency(Exception):
    def __init__(self, message: str, variables: str | None = None):
        super().__init__(message)
        self.variables = variables
