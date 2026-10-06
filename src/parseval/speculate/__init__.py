"""Seed instances from a query's compact IR before concolic generation."""

from .speculate import Speculator, speculate

__all__ = ("Speculator", "speculate")
