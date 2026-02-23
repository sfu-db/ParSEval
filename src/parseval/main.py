from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from parseval.db_manager import DBManager
from parseval.helper import compare_df

@dataclass
class PreCheckResult:
    state: str
    messages: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.state = self.state.upper()
    
class PreCheckingRule(ABC):
    def __init__(self, next_rule: Optional[PreCheckingRule] = None):
        self._next_rule = next_rule
        
    def set_next(self, next_rule: PreCheckingRule) -> PreCheckingRule:
        self._next_rule = next_rule
        return next_rule
    def check(
            self,
            schema: Any,
            gold: str,
            pred: Optional[str] = None,
            **kwargs
        ) -> PreCheckResult:
        """
        Execute current rule and optionally forward to next rule.
        """

        result = self._check(schema=schema, gold=gold, pred=pred, **kwargs)
        # Stop conditions
        if result.state in {"EQ", "NEQ", "SYNTAX_ERROR"}:
            return result

        # Continue chain
        if self._next_rule is not None:
            return self._next_rule.check(schema, gold, pred, **kwargs)

        return result
        
    @abstractmethod
    def _check(self, schema, gold: str, pred: Optional[str] = None, **kwargs) -> PreCheckResult:
        """
        Internal rule logic to be implemented by subclasses.
        """
        pass
    
    
class AstChecking(PreCheckingRule):
    def _check(self, schema, gold, pred = None, **kwargs):
        if pred is None:
            return PreCheckResult(state= "unknown")
        
        if gold == pred:
            return PreCheckResult(state= "eq")
        return PreCheckResult(state= "unknown")
        

class SyntaxChecking(PreCheckingRule):
    
    def _check(self, schema, gold, pred = None, **kwargs):
        
        host_or_path = kwargs.get("host_or_path")
        database = kwargs.get("database")
        dialect = kwargs.get("dialect")
        with DBManager().get_connection(host_or_path= host_or_path, database= database, dialect= dialect) as conn:
            try:
                conn.execute(gold)
            except Exception as e:
                return PreCheckResult(state= "syntax_error", messages={"gold_error": str(e)})
            if pred is not None:
                try:
                    conn.execute(pred)
                except Exception as e:
                    return PreCheckResult(state= "syntax_error", messages={"pred_error": str(e)})
        return PreCheckResult(state= "unknown")
   

class ResultChecking(PreCheckingRule):
    def __init__(self, next_rule = None, workspace = None):
        super().__init__(next_rule)
        
        self.workspace = workspace
        
    
    def _check(self, schema, gold, pred = None, **kwargs):
        if pred is None:
            return PreCheckResult(state= "unknown")
        
        return super()._check(schema, gold, pred, **kwargs)
    
class Disprover:
    def __init__(self, verbose=False):
        
        self._chain = AstChecking()
        self._chain.set_next(SyntaxChecking()).set_next(ResultChecking())
    
    def verify(self, schema, gold, pred = None, **kwargs) -> PreCheckResult:
        return self._chain.check(schema=schema, gold=gold, pred=pred, **kwargs)
        
