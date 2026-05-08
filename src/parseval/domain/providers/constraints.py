from __future__ import annotations

from typing import Any, Optional

from ..constraints import ChoicesConstraint
from .base import ValueProvider
from parseval.dtype import DataType


class ChoiceProvider(ValueProvider):
    """Handles explicit value lists defined in ChoicesConstraint."""
    priority = 20  # Higher than default type providers

    def supports(self, spec) -> int:
        if spec.datatype.is_type(DataType.Type.ENUM):
            return 20
        for check in spec.checks:
            if isinstance(check, ChoicesConstraint):
                return 20
        return 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0) -> Any:
        # Check for explicit ChoicesConstraint first
        constraint: Optional[ChoicesConstraint] = None
        for check in spec.checks:
            if isinstance(check, ChoicesConstraint):
                constraint = check
                break
        
        if constraint and constraint.values:
            return runtime.rng.choice(constraint.values)
            
        # Fallback to ENUM values if it's an ENUM type
        if spec.datatype.is_type(DataType.Type.ENUM):
            from sqlglot import exp
            values = [
                e.this if isinstance(e, exp.Literal) else str(e)
                for e in spec.datatype.args.get("expressions", [])
            ]
            if values:
                return runtime.rng.choice(values)
                
        return None
