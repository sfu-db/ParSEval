from __future__ import annotations

from typing import Tuple, List, Set, Dict, Optional, Callable, Union, NewType, Any
import enum
import json
from dataclasses import dataclass, field, asdict
from collections import defaultdict


class CoverageType(enum.Enum):
    PREDICATE = "Predicate"
    MULTIPLICITY = "Multiplicity"
    GROUP = 'Group'
    EXISTENCE = 'Existence'
    PATH = "Path"

OperatorKey = NewType('OperatorKey', str)
OperatorId = NewType('OperatorId', str)
ConstraintId = NewType('ConstraintId', str)

@dataclass
class CoverageEntry:
    """Represents a single coverage entry with detailed information"""
    operator_key: OperatorKey
    operator_id: OperatorId
    constraint_id: ConstraintId
    coverage_type: CoverageType
    label: Any = True
    details: Dict[str, Any] = field(default_factory=dict)
    parent_operator_id: Optional[OperatorId] = None
    timestamp: float = field(default_factory=lambda: 0.0)  # Can be used for ordering

class Coverage(object):
    """
    Enhanced class to track coverage of each operator in a query logical plan.
    Stores detailed information about execution paths and constraints.
    """

    def __init__(self) -> None:
        self._entries: List[CoverageEntry] = []
        self._operator_hierarchy: Dict[OperatorId, List[OperatorId]] = defaultdict(list)
        self._constraint_details: Dict[Tuple[OperatorKey, OperatorId, ConstraintId], Dict[str, Any]] = {}
        self._sql_conditions: Dict[Tuple[OperatorKey, OperatorId], List[Dict[str, Any]]] = defaultdict(list)
        self._case_when_paths: Dict[Tuple[OperatorKey, OperatorId, str], List[Dict[str, Any]]] = defaultdict(list)
    
    def trace(self) -> List[CoverageEntry]:
        """The list of executed entries with detailed information"""
        return self._entries

    def coverage(self) -> Set[Tuple[OperatorKey, OperatorId, ConstraintId]]:
        """The set of executed constraints and operators, as (operator_key, operator_id, constraint_id) tuples"""
        return set((entry.operator_key, entry.operator_id, entry.constraint_id) for entry in self._entries)

    def function_names(self) -> Set[str]:
        """The set of function names seen"""
        return set(entry.operator_key for entry in self._entries)
    
    def operator_names(self) -> Set[str]:
        """The set of operator names seen"""
        return set(f"{entry.operator_key}#{entry.operator_id}" for entry in self._entries)
    
    def operator_coverage(self) -> Dict[str, Set[ConstraintId]]:
        """Returns a dictionary mapping operator names to sets of constraint IDs they cover"""
        result = defaultdict(set)
        for entry in self._entries:
            op_name = f"{entry.operator_key}#{entry.operator_id}"
            result[op_name].add(entry.constraint_id)
        return result
    
    def coverage_by_type(self) -> Dict[CoverageType, List[CoverageEntry]]:
        """Returns a dictionary mapping coverage types to lists of entries"""
        result = defaultdict(list)
        for entry in self._entries:
            result[entry.coverage_type].append(entry)
        return result
    
    def get_operator_hierarchy(self) -> Dict[OperatorId, List[OperatorId]]:
        """Returns the hierarchical structure of operators"""
        return self._operator_hierarchy
    
    def add_operator_relationship(self, parent_id: OperatorId, child_id: OperatorId) -> None:
        """Adds a parent-child relationship between operators"""
        if child_id not in self._operator_hierarchy[parent_id]:
            self._operator_hierarchy[parent_id].append(child_id)
    
    def traceit(self, 
                operator_key: OperatorKey, 
                operator_id: OperatorId, 
                constraint_id: ConstraintId, 
                event: Union[CoverageType, str] = CoverageType.PATH, 
                label: Any = True,
                details: Optional[Dict[str, Any]] = None,
                parent_operator_id: Optional[OperatorId] = None) -> Optional[Callable]:
        """
        Records a coverage entry with detailed information
        
        Args:
            operator_key: The key of the operator
            operator_id: The ID of the operator
            constraint_id: The ID of the constraint
            event: The type of coverage event
            label: Additional label information
            details: Additional details about the constraint
            parent_operator_id: The ID of the parent operator (if any)
            
        Returns:
            The traceit method for chaining
        """
        coverage_type = self._assert_coverage_type(event)
        
        entry = CoverageEntry(
            operator_key=operator_key,
            operator_id=operator_id,
            constraint_id=constraint_id,
            coverage_type=coverage_type,
            label=label,
            details=details or {},
            parent_operator_id=parent_operator_id
        )
        
        self._entries.append(entry)
        
        # Store constraint details for later retrieval
        key = (operator_key, operator_id, constraint_id)
        if key not in self._constraint_details:
            self._constraint_details[key] = {}
        
        # Update constraint details with any new information
        if details:
            self._constraint_details[key].update(details)
        
        # Record parent-child relationship if provided
        if parent_operator_id:
            self.add_operator_relationship(parent_operator_id, operator_id)
            
        return self.traceit
    
    def trace_sql_condition(self, 
                          operator_key: OperatorKey, 
                          operator_id: OperatorId, 
                          sql_condition: str,
                          condition_type: str,
                          condition_result: bool,
                          details: Optional[Dict[str, Any]] = None) -> None:
        """
        Records a SQL condition with detailed information
        
        Args:
            operator_key: The key of the operator
            operator_id: The ID of the operator
            sql_condition: The SQL condition string
            condition_type: The type of condition (e.g., 'WHERE', 'JOIN', 'HAVING')
            condition_result: Whether the condition was satisfied
            details: Additional details about the condition
        """
        condition_info = {
            "sql_condition": sql_condition,
            "condition_type": condition_type,
            "condition_result": condition_result,
            "timestamp": 0.0  # Can be updated with actual timestamp if needed
        }
        
        if details:
            condition_info.update(details)
            
        self._sql_conditions[(operator_key, operator_id)].append(condition_info)
        
        # Also record as a coverage entry for consistency
        self.traceit(
            operator_key=operator_key,
            operator_id=operator_id,
            constraint_id=f"sql_condition_{len(self._sql_conditions[(operator_key, operator_id)])}",
            event=CoverageType.SQL_CONDITION,
            label=condition_result,
            details=condition_info
        )
    
    def get_sql_conditions(self, operator_key: OperatorKey, operator_id: OperatorId) -> List[Dict[str, Any]]:
        """
        Returns all SQL conditions for a specific operator
        
        Args:
            operator_key: The key of the operator
            operator_id: The ID of the operator
            
        Returns:
            List of SQL condition information dictionaries
        """
        return self._sql_conditions.get((operator_key, operator_id), [])
    
    def trace_case_when(self, 
                       operator_key: OperatorKey, 
                       operator_id: OperatorId, 
                       case_id: str,
                       condition: str,
                       condition_result: bool,
                       branch_taken: bool,
                       details: Optional[Dict[str, Any]] = None) -> None:
        """
        Records a CASE WHEN execution path
        
        Args:
            operator_key: The key of the operator
            operator_id: The ID of the operator
            case_id: The ID of the CASE expression
            condition: The condition being evaluated
            condition_result: The result of the condition evaluation
            branch_taken: Whether this branch was taken
            details: Additional details about the CASE WHEN execution
        """
        case_info = {
            "case_id": case_id,
            "condition": condition,
            "condition_result": condition_result,
            "branch_taken": branch_taken,
            "timestamp": 0.0  # Can be updated with actual timestamp if needed
        }
        
        if details:
            case_info.update(details)
            
        self._case_when_paths[(operator_key, operator_id, case_id)].append(case_info)
        
        # Also record as a coverage entry for consistency
        self.traceit(
            operator_key=operator_key,
            operator_id=operator_id,
            constraint_id=f"case_when_{case_id}",
            event=CoverageType.CASE_WHEN,
            label=branch_taken,
            details=case_info
        )
    
    def get_case_when_paths(self, operator_key: OperatorKey, operator_id: OperatorId, case_id: str) -> List[Dict[str, Any]]:
        """
        Returns all execution paths for a specific CASE WHEN expression
        
        Args:
            operator_key: The key of the operator
            operator_id: The ID of the operator
            case_id: The ID of the CASE expression
            
        Returns:
            List of CASE WHEN execution path information dictionaries
        """
        return self._case_when_paths.get((operator_key, operator_id, case_id), [])
    
    def get_constraint_details(self, operator_key: OperatorKey, operator_id: OperatorId, constraint_id: ConstraintId) -> Dict[str, Any]:
        """Returns detailed information about a specific constraint"""
        key = (operator_key, operator_id, constraint_id)
        return self._constraint_details.get(key, {})
    
    def get_operator_entries(self, operator_key: OperatorKey, operator_id: OperatorId) -> List[CoverageEntry]:
        """Returns all coverage entries for a specific operator"""
        return [entry for entry in self._entries 
                if entry.operator_key == operator_key and entry.operator_id == operator_id]
    
    def get_constraint_entries(self, constraint_id: ConstraintId) -> List[CoverageEntry]:
        """Returns all coverage entries for a specific constraint"""
        return [entry for entry in self._entries if entry.constraint_id == constraint_id]
    
    def to_dict(self) -> Dict[str, Any]:
        """Converts the coverage data to a dictionary for serialization"""
        return {
            "entries": [asdict(entry) for entry in self._entries],
            "operator_hierarchy": self._operator_hierarchy,
            "constraint_details": {f"{k[0]}#{k[1]}#{k[2]}": v for k, v in self._constraint_details.items()},
            "sql_conditions": {f"{k[0]}#{k[1]}": v for k, v in self._sql_conditions.items()},
            "case_when_paths": {f"{k[0]}#{k[1]}#{k[2]}": v for k, v in self._case_when_paths.items()}
        }
    
    def save_to_file(self, filename: str) -> None:
        """Saves the coverage data to a JSON file"""
        with open(filename, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load_from_file(cls, filename: str) -> 'Coverage':
        """Loads coverage data from a JSON file"""
        with open(filename, 'r') as f:
            data = json.load(f)
        
        coverage = cls()
        
        # Reconstruct entries
        for entry_dict in data.get("entries", []):
            entry = CoverageEntry(
                operator_key=entry_dict["operator_key"],
                operator_id=entry_dict["operator_id"],
                constraint_id=entry_dict["constraint_id"],
                coverage_type=CoverageType(entry_dict["coverage_type"]),
                label=entry_dict["label"],
                details=entry_dict["details"],
                parent_operator_id=entry_dict["parent_operator_id"]
            )
            coverage._entries.append(entry)
        
        # Reconstruct operator hierarchy
        coverage._operator_hierarchy = data.get("operator_hierarchy", {})
        
        # Reconstruct constraint details
        for key_str, details in data.get("constraint_details", {}).items():
            parts = key_str.split("#")
            if len(parts) == 3:
                key = (parts[0], parts[1], parts[2])
                coverage._constraint_details[key] = details
        
        # Reconstruct SQL conditions
        for key_str, conditions in data.get("sql_conditions", {}).items():
            parts = key_str.split("#")
            if len(parts) == 2:
                key = (parts[0], parts[1])
                coverage._sql_conditions[key] = conditions
        
        # Reconstruct CASE WHEN paths
        for key_str, paths in data.get("case_when_paths", {}).items():
            parts = key_str.split("#")
            if len(parts) == 3:
                key = (parts[0], parts[1], parts[2])
                coverage._case_when_paths[key] = paths
        
        return coverage
    
    def _assert_coverage_type(self, event: Union[CoverageType, str]) -> CoverageType:
        """Converts a string to a CoverageType if needed"""
        if isinstance(event, CoverageType):
            return event        
        
        return CoverageType[str(event).capitalize()]