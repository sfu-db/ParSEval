from typing import Dict, List, Set, Any, Optional
import json
import matplotlib.pyplot as plt
import networkx as nx
from .coverage import Coverage, CoverageType, CoverageEntry

class CoverageAnalyzer:
    """
    Utility class for analyzing and visualizing coverage data from query execution.
    """
    
    @staticmethod
    def generate_coverage_report(coverage: Coverage) -> Dict[str, Any]:
        """
        Generates a comprehensive coverage report.
        
        Args:
            coverage: The Coverage object containing execution data
            
        Returns:
            A dictionary containing various coverage metrics
        """
        report = {
            "total_entries": len(coverage.trace()),
            "operator_coverage": {},
            "coverage_by_type": {},
            "constraint_details": {},
            "operator_hierarchy": coverage.get_operator_hierarchy(),
            "case_when_coverage": {},
            "sql_condition_coverage": {}
        }
        
        # Calculate operator coverage
        for op_name, constraint_ids in coverage.operator_coverage().items():
            report["operator_coverage"][op_name] = {
                "constraint_count": len(constraint_ids),
                "constraints": list(constraint_ids)
            }
        
        # Calculate coverage by type
        for cov_type, entries in coverage.coverage_by_type().items():
            report["coverage_by_type"][cov_type.value] = {
                "count": len(entries),
                "operators": list(set(f"{entry.operator_key}#{entry.operator_id}" for entry in entries))
            }
        
        # Collect constraint details
        for entry in coverage.trace():
            key = f"{entry.operator_key}#{entry.operator_id}#{entry.constraint_id}"
            if key not in report["constraint_details"]:
                report["constraint_details"][key] = entry.details
        
        # Collect CASE WHEN coverage
        for entry in coverage.trace():
            if entry.coverage_type == CoverageType.CASE_WHEN:
                op_key = f"{entry.operator_key}#{entry.operator_id}"
                case_id = entry.details.get("case_id", "unknown")
                
                if op_key not in report["case_when_coverage"]:
                    report["case_when_coverage"][op_key] = {}
                
                if case_id not in report["case_when_coverage"][op_key]:
                    report["case_when_coverage"][op_key][case_id] = {
                        "conditions": [],
                        "branches_taken": 0,
                        "total_branches": 0
                    }
                
                case_data = report["case_when_coverage"][op_key][case_id]
                condition = entry.details.get("condition", "unknown")
                
                if condition not in case_data["conditions"]:
                    case_data["conditions"].append(condition)
                
                case_data["total_branches"] += 1
                if entry.details.get("branch_taken", False):
                    case_data["branches_taken"] += 1
        
        # Collect SQL condition coverage
        for entry in coverage.trace():
            if entry.coverage_type == CoverageType.SQL_CONDITION:
                op_key = f"{entry.operator_key}#{entry.operator_id}"
                
                if op_key not in report["sql_condition_coverage"]:
                    report["sql_condition_coverage"][op_key] = {
                        "conditions": [],
                        "condition_types": {},
                        "satisfied_count": 0,
                        "total_count": 0
                    }
                
                sql_data = report["sql_condition_coverage"][op_key]
                condition = entry.details.get("sql_condition", "unknown")
                condition_type = entry.details.get("condition_type", "unknown")
                
                if condition not in sql_data["conditions"]:
                    sql_data["conditions"].append(condition)
                
                if condition_type not in sql_data["condition_types"]:
                    sql_data["condition_types"][condition_type] = {
                        "count": 0,
                        "satisfied": 0
                    }
                
                sql_data["condition_types"][condition_type]["count"] += 1
                sql_data["total_count"] += 1
                
                if entry.details.get("condition_result", False):
                    sql_data["condition_types"][condition_type]["satisfied"] += 1
                    sql_data["satisfied_count"] += 1
        
        return report
    
    @staticmethod
    def save_report(report: Dict[str, Any], filename: str) -> None:
        """
        Saves a coverage report to a JSON file.
        
        Args:
            report: The coverage report to save
            filename: The filename to save to
        """
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)
    
    @staticmethod
    def load_report(filename: str) -> Dict[str, Any]:
        """
        Loads a coverage report from a JSON file.
        
        Args:
            filename: The filename to load from
            
        Returns:
            The loaded coverage report
        """
        with open(filename, 'r') as f:
            return json.load(f)
    
    @staticmethod
    def visualize_operator_hierarchy(coverage: Coverage, filename: Optional[str] = None) -> None:
        """
        Visualizes the operator hierarchy as a directed graph.
        
        Args:
            coverage: The Coverage object containing execution data
            filename: Optional filename to save the visualization to
        """
        G = nx.DiGraph()
        
        # Add nodes for all operators
        for entry in coverage.trace():
            op_name = f"{entry.operator_key}#{entry.operator_id}"
            G.add_node(op_name, operator_key=entry.operator_key, operator_id=entry.operator_id)
        
        # Add edges for parent-child relationships
        for parent_id, children in coverage.get_operator_hierarchy().items():
            for child_id in children:
                parent_name = None
                child_name = None
                
                # Find the operator names
                for entry in coverage.trace():
                    if entry.operator_id == parent_id:
                        parent_name = f"{entry.operator_key}#{entry.operator_id}"
                    if entry.operator_id == child_id:
                        child_name = f"{entry.operator_key}#{entry.operator_id}"
                
                if parent_name and child_name:
                    G.add_edge(parent_name, child_name)
        
        # Create the visualization
        plt.figure(figsize=(12, 8))
        pos = nx.spring_layout(G, seed=42)
        
        # Draw nodes
        nx.draw_networkx_nodes(G, pos, node_size=700, node_color='lightblue')
        
        # Draw edges
        nx.draw_networkx_edges(G, pos, arrows=True, arrowsize=20)
        
        # Draw labels
        nx.draw_networkx_labels(G, pos, font_size=10)
        
        plt.title("Query Operator Hierarchy")
        plt.axis('off')
        
        if filename:
            plt.savefig(filename)
            plt.close()
        else:
            plt.show()
    
    @staticmethod
    def visualize_coverage_by_type(coverage: Coverage, filename: Optional[str] = None) -> None:
        """
        Visualizes coverage by type as a bar chart.
        
        Args:
            coverage: The Coverage object containing execution data
            filename: Optional filename to save the visualization to
        """
        coverage_by_type = coverage.coverage_by_type()
        
        types = [t.value for t in coverage_by_type.keys()]
        counts = [len(entries) for entries in coverage_by_type.values()]
        
        plt.figure(figsize=(10, 6))
        plt.bar(types, counts, color='skyblue')
        plt.xlabel('Coverage Type')
        plt.ylabel('Count')
        plt.title('Coverage by Type')
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        if filename:
            plt.savefig(filename)
            plt.close()
        else:
            plt.show()
    
    @staticmethod
    def visualize_case_when_coverage(coverage: Coverage, filename: Optional[str] = None) -> None:
        """
        Visualizes CASE WHEN coverage as a bar chart.
        
        Args:
            coverage: The Coverage object containing execution data
            filename: Optional filename to save the visualization to
        """
        # Get all CASE WHEN entries
        case_when_entries = [entry for entry in coverage.trace() 
                            if entry.coverage_type == CoverageType.CASE_WHEN]
        
        if not case_when_entries:
            print("No CASE WHEN entries found in coverage data")
            return
        
        # Group by operator
        operator_cases = {}
        for entry in case_when_entries:
            op_key = f"{entry.operator_key}#{entry.operator_id}"
            case_id = entry.details.get("case_id", "unknown")
            
            if op_key not in operator_cases:
                operator_cases[op_key] = {}
            
            if case_id not in operator_cases[op_key]:
                operator_cases[op_key][case_id] = {
                    "conditions": set(),
                    "branches_taken": 0,
                    "total_branches": 0
                }
            
            case_data = operator_cases[op_key][case_id]
            condition = entry.details.get("condition", "unknown")
            case_data["conditions"].add(condition)
            case_data["total_branches"] += 1
            
            if entry.details.get("branch_taken", False):
                case_data["branches_taken"] += 1
        
        # Prepare data for visualization
        operators = []
        case_counts = []
        branch_coverage = []
        
        for op_key, cases in operator_cases.items():
            operators.append(op_key)
            case_counts.append(len(cases))
            
            # Calculate branch coverage percentage
            total_branches = sum(case_data["total_branches"] for case_data in cases.values())
            taken_branches = sum(case_data["branches_taken"] for case_data in cases.values())
            coverage_pct = (taken_branches / total_branches * 100) if total_branches > 0 else 0
            branch_coverage.append(coverage_pct)
        
        # Create the visualization
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
        
        # Plot number of CASE expressions per operator
        ax1.bar(operators, case_counts, color='skyblue')
        ax1.set_xlabel('Operator')
        ax1.set_ylabel('Number of CASE Expressions')
        ax1.set_title('CASE Expressions per Operator')
        ax1.tick_params(axis='x', rotation=45)
        
        # Plot branch coverage percentage
        ax2.bar(operators, branch_coverage, color='lightgreen')
        ax2.set_xlabel('Operator')
        ax2.set_ylabel('Branch Coverage (%)')
        ax2.set_title('CASE WHEN Branch Coverage')
        ax2.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        
        if filename:
            plt.savefig(filename)
            plt.close()
        else:
            plt.show()
    
    @staticmethod
    def visualize_sql_condition_coverage(coverage: Coverage, filename: Optional[str] = None) -> None:
        """
        Visualizes SQL condition coverage as a bar chart.
        
        Args:
            coverage: The Coverage object containing execution data
            filename: Optional filename to save the visualization to
        """
        # Get all SQL condition entries
        sql_condition_entries = [entry for entry in coverage.trace() 
                                if entry.coverage_type == CoverageType.SQL_CONDITION]
        
        if not sql_condition_entries:
            print("No SQL condition entries found in coverage data")
            return
        
        # Group by operator and condition type
        operator_conditions = {}
        for entry in sql_condition_entries:
            op_key = f"{entry.operator_key}#{entry.operator_id}"
            condition_type = entry.details.get("condition_type", "unknown")
            
            if op_key not in operator_conditions:
                operator_conditions[op_key] = {
                    "conditions": set(),
                    "condition_types": defaultdict(lambda: {"count": 0, "satisfied": 0}),
                    "total_count": 0,
                    "satisfied_count": 0
                }
            
            op_data = operator_conditions[op_key]
            condition = entry.details.get("sql_condition", "unknown")
            op_data["conditions"].add(condition)
            op_data["condition_types"][condition_type]["count"] += 1
            op_data["total_count"] += 1
            
            if entry.details.get("condition_result", False):
                op_data["condition_types"][condition_type]["satisfied"] += 1
                op_data["satisfied_count"] += 1
        
        # Prepare data for visualization
        operators = []
        condition_counts = []
        satisfaction_rates = []
        
        for op_key, data in operator_conditions.items():
            operators.append(op_key)
            condition_counts.append(len(data["conditions"]))
            
            # Calculate satisfaction rate
            satisfaction_rate = (data["satisfied_count"] / data["total_count"] * 100) if data["total_count"] > 0 else 0
            satisfaction_rates.append(satisfaction_rate)
        
        # Create the visualization
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
        
        # Plot number of unique conditions per operator
        ax1.bar(operators, condition_counts, color='skyblue')
        ax1.set_xlabel('Operator')
        ax1.set_ylabel('Number of Unique Conditions')
        ax1.set_title('SQL Conditions per Operator')
        ax1.tick_params(axis='x', rotation=45)
        
        # Plot condition satisfaction rate
        ax2.bar(operators, satisfaction_rates, color='lightgreen')
        ax2.set_xlabel('Operator')
        ax2.set_ylabel('Condition Satisfaction Rate (%)')
        ax2.set_title('SQL Condition Satisfaction Rate')
        ax2.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        
        if filename:
            plt.savefig(filename)
            plt.close()
        else:
            plt.show()
    
    @staticmethod
    def find_uncovered_operators(all_operators: Set[str], coverage: Coverage) -> Set[str]:
        """
        Finds operators that are not covered in the execution.
        
        Args:
            all_operators: Set of all possible operator names
            coverage: The Coverage object containing execution data
            
        Returns:
            Set of uncovered operator names
        """
        covered_operators = coverage.operator_names()
        return all_operators - covered_operators
    
    @staticmethod
    def find_constraint_violations(coverage: Coverage) -> List[Dict[str, Any]]:
        """
        Finds constraints that were violated during execution.
        
        Args:
            coverage: The Coverage object containing execution data
            
        Returns:
            List of dictionaries containing information about violated constraints
        """
        violations = []
        
        for entry in coverage.trace():
            if entry.label is False:  # Assuming False indicates a violation
                violation = {
                    "operator_key": entry.operator_key,
                    "operator_id": entry.operator_id,
                    "constraint_id": entry.constraint_id,
                    "coverage_type": entry.coverage_type.value,
                    "details": entry.details
                }
                violations.append(violation)
        
        return violations
    
    @staticmethod
    def analyze_case_when_coverage(coverage: Coverage) -> Dict[str, Any]:
        """
        Analyzes the coverage of CASE WHEN operations.
        
        Args:
            coverage: The Coverage object containing execution data
            
        Returns:
            A dictionary containing analysis of CASE WHEN coverage
        """
        # Get all CASE WHEN entries
        case_when_entries = [entry for entry in coverage.trace() 
                            if entry.coverage_type == CoverageType.CASE_WHEN]
        
        if not case_when_entries:
            return {"message": "No CASE WHEN entries found in coverage data"}
        
        # Group by operator and case ID
        operator_cases = {}
        for entry in case_when_entries:
            op_key = f"{entry.operator_key}#{entry.operator_id}"
            case_id = entry.details.get("case_id", "unknown")
            
            if op_key not in operator_cases:
                operator_cases[op_key] = {}
            
            if case_id not in operator_cases[op_key]:
                operator_cases[op_key][case_id] = {
                    "conditions": set(),
                    "branches_taken": 0,
                    "total_branches": 0,
                    "paths": []
                }
            
            case_data = operator_cases[op_key][case_id]
            condition = entry.details.get("condition", "unknown")
            case_data["conditions"].add(condition)
            case_data["total_branches"] += 1
            
            if entry.details.get("branch_taken", False):
                case_data["branches_taken"] += 1
            
            # Store the execution path
            case_data["paths"].append({
                "condition": condition,
                "condition_result": entry.details.get("condition_result", False),
                "branch_taken": entry.details.get("branch_taken", False),
                "details": {k: v for k, v in entry.details.items() 
                           if k not in ["case_id", "condition", "condition_result", "branch_taken"]}
            })
        
        # Calculate coverage metrics
        analysis = {
            "total_case_expressions": sum(len(cases) for cases in operator_cases.values()),
            "operators_with_cases": len(operator_cases),
            "operator_details": {}
        }
        
        for op_key, cases in operator_cases.items():
            total_branches = sum(case_data["total_branches"] for case_data in cases.values())
            taken_branches = sum(case_data["branches_taken"] for case_data in cases.values())
            coverage_pct = (taken_branches / total_branches * 100) if total_branches > 0 else 0
            
            analysis["operator_details"][op_key] = {
                "case_count": len(cases),
                "total_branches": total_branches,
                "taken_branches": taken_branches,
                "coverage_percentage": coverage_pct,
                "cases": {
                    case_id: {
                        "condition_count": len(case_data["conditions"]),
                        "branches_taken": case_data["branches_taken"],
                        "total_branches": case_data["total_branches"],
                        "coverage_percentage": (case_data["branches_taken"] / case_data["total_branches"] * 100) 
                                             if case_data["total_branches"] > 0 else 0,
                        "conditions": list(case_data["conditions"]),
                        "paths": case_data["paths"]
                    }
                    for case_id, case_data in cases.items()
                }
            }
        
        return analysis
    
    @staticmethod
    def analyze_sql_condition_coverage(coverage: Coverage) -> Dict[str, Any]:
        """
        Analyzes the coverage of SQL conditions in operators.
        
        Args:
            coverage: The Coverage object containing execution data
            
        Returns:
            A dictionary containing analysis of SQL condition coverage
        """
        # Get all SQL condition entries
        sql_condition_entries = [entry for entry in coverage.trace() 
                                if entry.coverage_type == CoverageType.SQL_CONDITION]
        
        if not sql_condition_entries:
            return {"message": "No SQL condition entries found in coverage data"}
        
        # Group by operator and condition type
        operator_conditions = {}
        for entry in sql_condition_entries:
            op_key = f"{entry.operator_key}#{entry.operator_id}"
            condition_type = entry.details.get("condition_type", "unknown")
            
            if op_key not in operator_conditions:
                operator_conditions[op_key] = {
                    "conditions": set(),
                    "condition_types": defaultdict(lambda: {"count": 0, "satisfied": 0}),
                    "total_count": 0,
                    "satisfied_count": 0,
                    "details": []
                }
            
            op_data = operator_conditions[op_key]
            condition = entry.details.get("sql_condition", "unknown")
            op_data["conditions"].add(condition)
            op_data["condition_types"][condition_type]["count"] += 1
            op_data["total_count"] += 1
            
            if entry.details.get("condition_result", False):
                op_data["condition_types"][condition_type]["satisfied"] += 1
                op_data["satisfied_count"] += 1
            
            # Store the condition details
            op_data["details"].append({
                "condition": condition,
                "condition_type": condition_type,
                "condition_result": entry.details.get("condition_result", False),
                "details": {k: v for k, v in entry.details.items() 
                           if k not in ["sql_condition", "condition_type", "condition_result"]}
            })
        
        # Calculate coverage metrics
        analysis = {
            "total_operators_with_conditions": len(operator_conditions),
            "total_unique_conditions": sum(len(data["conditions"]) for data in operator_conditions.values()),
            "total_condition_evaluations": sum(data["total_count"] for data in operator_conditions.values()),
            "total_satisfied_conditions": sum(data["satisfied_count"] for data in operator_conditions.values()),
            "overall_satisfaction_rate": sum(data["satisfied_count"] for data in operator_conditions.values()) / 
                                       sum(data["total_count"] for data in operator_conditions.values()) * 100 
                                       if sum(data["total_count"] for data in operator_conditions.values()) > 0 else 0,
            "operator_details": {}
        }
        
        for op_key, data in operator_conditions.items():
            satisfaction_rate = (data["satisfied_count"] / data["total_count"] * 100) if data["total_count"] > 0 else 0
            
            analysis["operator_details"][op_key] = {
                "unique_conditions": len(data["conditions"]),
                "total_evaluations": data["total_count"],
                "satisfied_evaluations": data["satisfied_count"],
                "satisfaction_rate": satisfaction_rate,
                "condition_types": {
                    cond_type: {
                        "count": type_data["count"],
                        "satisfied": type_data["satisfied"],
                        "satisfaction_rate": (type_data["satisfied"] / type_data["count"] * 100) 
                                            if type_data["count"] > 0 else 0
                    }
                    for cond_type, type_data in data["condition_types"].items()
                },
                "conditions": list(data["conditions"]),
                "details": data["details"]
            }
        
        return analysis 