"""
Example demonstrating how to use the enhanced constraint tree system
to capture all predicates in SQL operators.

This example shows:
1. How to set up the constraint tree
2. How to capture predicates from different operators
3. How to analyze coverage and generate reports
4. How to use the system for query optimization
"""

from typing import List, Dict, Any
from .constraint import Constraint, PlausibleChild
from .constant import BranchType, PathConstraintType, Action
from .uexpr_to_constraint import UExprToConstraint
from sqlglot import exp
from src.expression.symbol import Expr, Literal
import logging

logger = logging.getLogger(__name__)

class EnhancedConstraintTree:
    """
    Enhanced constraint tree system for comprehensive predicate capture.
    
    This class provides a high-level interface for managing constraint trees
    and analyzing predicate coverage across SQL operators.
    """
    
    def __init__(self):
        self.tree = UExprToConstraint(add=None)  # We'll handle add separately
        self.operator_stats: Dict[str, Dict[str, Any]] = {}
        self.coverage_history: List[Dict[str, Any]] = []
        
    def capture_filter_predicates(self, operator_key: str, operator_id: str, 
                                condition: exp.Condition, row_data: List[Any],
                                metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Capture all predicates from a filter operator.
        
        Args:
            operator_key: The operator type (e.g., 'filter')
            operator_id: Unique identifier for the operator
            condition: SQL condition to analyze
            row_data: Data that satisfies the condition
            metadata: Additional metadata about the operator
            
        Returns:
            List of predicate information dictionaries
        """
        predicates = []
        
        # Extract all predicate expressions from the condition
        sql_predicates = list(condition.find_all(exp.Predicate))
        
        for i, sql_pred in enumerate(sql_predicates):
            # Create symbolic expression (simplified for example)
            symbolic_expr = self._create_symbolic_expression(sql_pred)
            
            # Determine if this predicate was taken
            taken = self._evaluate_predicate(sql_pred, row_data)
            
            # Analyze constraint type
            constraint_type = self._analyze_constraint_type(sql_pred)
            
            # Create predicate info dictionary
            predicate_info = {
                'symbolic_expr': symbolic_expr,
                'sql_condition': sql_pred,
                'taken': taken,
                'operator_key': operator_key,
                'operator_i': operator_id,
                'constraint_type': constraint_type,
                'metadata': metadata
            }
            
            predicates.append(predicate_info)
            
        return predicates
    
    def capture_join_predicates(self, operator_key: str, operator_id: str,
                              condition: exp.Condition, left_data: List[Any],
                              right_data: List[Any], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Capture all predicates from a join operator.
        
        Args:
            operator_key: The operator type (e.g., 'join')
            operator_id: Unique identifier for the operator
            condition: Join condition to analyze
            left_data: Data from left table
            right_data: Data from right table
            metadata: Additional metadata about the operator
            
        Returns:
            List of predicate information dictionaries
        """
        predicates = []
        
        # Extract join predicates
        sql_predicates = list(condition.find_all(exp.Predicate))
        
        for i, sql_pred in enumerate(sql_predicates):
            # Create symbolic expression
            symbolic_expr = self._create_symbolic_expression(sql_pred)
            
            # Evaluate join predicate for all combinations
            for l_row in left_data:
                for r_row in right_data:
                    combined_row = l_row + r_row  # Simplified combination
                    taken = self._evaluate_predicate(sql_pred, combined_row)
                    
                    constraint_type = self._analyze_constraint_type(sql_pred)
                    
                    predicate_info = {
                        'symbolic_expr': symbolic_expr,
                        'sql_condition': sql_pred,
                        'taken': taken,
                        'operator_key': operator_key,
                        'operator_i': operator_id,
                        'constraint_type': constraint_type,
                        'metadata': metadata
                    }
                    
                    predicates.append(predicate_info)
        
        return predicates
    
    def _create_symbolic_expression(self, sql_pred: exp.Predicate) -> Expr:
        """Create a symbolic expression from a SQL predicate."""
        # This is a simplified implementation
        # In practice, you would use your symbolic expression system
        if isinstance(sql_pred, exp.EQ):
            return Literal(True)  # Simplified
        elif isinstance(sql_pred, exp.GT):
            return Literal(True)  # Simplified
        else:
            return Literal(True)  # Default
    
    def _evaluate_predicate(self, sql_pred: exp.Predicate, row_data: List[Any]) -> bool:
        """Evaluate a SQL predicate against row data."""
        # This is a simplified implementation
        # In practice, you would use your evaluation system
        return True  # Simplified
    
    def _analyze_constraint_type(self, sql_pred: exp.Predicate) -> PathConstraintType:
        """Analyze the type of constraint based on the SQL predicate."""
        if isinstance(sql_pred, (exp.Count, exp.Exists)):
            return PathConstraintType.SIZE
        elif isinstance(sql_pred, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            if isinstance(sql_pred.expression, exp.Literal):
                return PathConstraintType.VALUE
            elif isinstance(sql_pred.this, exp.Column) and isinstance(sql_pred.expression, exp.Column):
                return PathConstraintType.PATH
        return PathConstraintType.VALUE
    
    def add_constraint_to_tree(self, parent_node: Constraint, predicate_info: Dict[str, Any],
                             tuples: List[Any]) -> Constraint:
        """
        Add a constraint to the tree with comprehensive tracking.
        
        Args:
            parent_node: Parent constraint node
            predicate_info: Information about the predicate
            tuples: Tuple data that satisfies the predicate
            
        Returns:
            The created constraint node
        """
        # Create child node
        child_node = parent_node.add_child(
            predicate_info['operator_key'],
            predicate_info['operator_i'],
            predicate_info['sql_condition'],
            predicate_info['symbolic_expr'],
            branch=predicate_info['taken'],
            info=predicate_info['metadata'],
            taken=predicate_info['taken'],
            tuples=tuples
        )
        
        # Update statistics
        self._update_operator_stats(predicate_info)
        
        return child_node
    
    def _update_operator_stats(self, predicate_info: Dict[str, Any]):
        """Update statistics for the operator."""
        op_key = f"{predicate_info['operator_key']}_{predicate_info['operator_i']}"
        
        if op_key not in self.operator_stats:
            self.operator_stats[op_key] = {
                'total_predicates': 0,
                'covered_predicates': 0,
                'constraint_types': {},
                'complexity_scores': []
            }
        
        stats = self.operator_stats[op_key]
        stats['total_predicates'] += 1
        
        # Track constraint types
        constraint_type = str(predicate_info['constraint_type'])
        stats['constraint_types'][constraint_type] = stats['constraint_types'].get(constraint_type, 0) + 1
    
    def generate_comprehensive_report(self) -> Dict[str, Any]:
        """Generate a comprehensive report of the constraint tree."""
        # Get basic coverage report
        coverage_report = self.tree.get_coverage_report()
        
        # Add operator-specific statistics
        coverage_report['operator_statistics'] = self.operator_stats
        
        # Add coverage history
        coverage_report['coverage_history'] = self.coverage_history
        
        # Add recommendations for improvement
        coverage_report['improvement_recommendations'] = self._generate_improvement_recommendations(coverage_report)
        
        return coverage_report
    
    def _generate_improvement_recommendations(self, report: Dict[str, Any]) -> List[str]:
        """Generate specific recommendations for improving coverage."""
        recommendations = []
        
        # Analyze operator coverage
        for op_key, stats in report['operator_statistics'].items():
            coverage_ratio = stats['covered_predicates'] / stats['total_predicates'] if stats['total_predicates'] > 0 else 0
            
            if coverage_ratio < 0.5:
                recommendations.append(f"Low coverage for {op_key}: {coverage_ratio:.2%}. Focus on this operator.")
            
            # Check constraint type distribution
            if 'VALUE' in stats['constraint_types'] and stats['constraint_types']['VALUE'] > stats['total_predicates'] * 0.8:
                recommendations.append(f"{op_key} has mostly VALUE constraints. Consider adding PATH or SIZE constraints.")
        
        # Analyze complexity
        avg_complexity = sum(report['complexity_distribution'].values()) / len(report['complexity_distribution']) if report['complexity_distribution'] else 0
        if avg_complexity > 3.0:
            recommendations.append("High average complexity detected. Consider simplifying query structure.")
        
        return recommendations
    
    def find_uncovered_paths(self) -> List[Dict[str, Any]]:
        """Find paths in the constraint tree that haven't been covered."""
        uncovered_paths = []
        
        def traverse_node(node: Constraint, path: List[str]):
            # Check if this node is covered
            if not node.is_covered():
                uncovered_paths.append({
                    'path': path + [node.pattern()],
                    'operator': f"{node.operator_key}_{node.operator_i}",
                    'constraint_type': node.constraint_type,
                    'complexity': node.complexity_score,
                    'tables': node.get_tables(),
                    'sql_condition': str(node.sql_condition) if node.sql_condition else None
                })
            
            # Traverse children
            for child in node.children.values():
                if isinstance(child, Constraint):
                    traverse_node(child, path + [node.pattern()])
        
        traverse_node(self.tree.root_constraint, [])
        return uncovered_paths
    
    def suggest_test_cases(self, uncovered_paths: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Suggest test cases to cover uncovered paths."""
        test_cases = []
        
        for path_info in uncovered_paths:
            # Generate test case based on path characteristics
            test_case = {
                'target_path': path_info['path'],
                'operator': path_info['operator'],
                'constraint_type': path_info['constraint_type'],
                'suggested_data': self._generate_suggested_data(path_info),
                'priority': self._calculate_test_priority(path_info)
            }
            test_cases.append(test_case)
        
        return test_cases
    
    def _generate_suggested_data(self, path_info: Dict[str, Any]) -> Dict[str, Any]:
        """Generate suggested test data for covering a path."""
        # This is a simplified implementation
        # In practice, you would use more sophisticated data generation
        return {
            'tables': path_info['tables'],
            'constraints': [],
            'expected_result': 'varies'
        }
    
    def _calculate_test_priority(self, path_info: Dict[str, Any]) -> float:
        """Calculate priority for testing a path."""
        priority = 1.0
        
        # Higher priority for high-complexity paths
        priority += path_info['complexity'] * 0.5
        
        # Higher priority for paths with more tables
        priority += len(path_info['tables']) * 0.3
        
        # Higher priority for certain constraint types
        if path_info['constraint_type'] == PathConstraintType.SIZE:
            priority += 0.5
        
        return min(priority, 5.0)  # Cap at 5.0

# Example usage
def example_usage():
    """Example of how to use the enhanced constraint tree system."""
    
    # Create the constraint tree
    constraint_tree = EnhancedConstraintTree()
    
    # Example: Capture predicates from a filter operator
    filter_condition = exp.EQ(
        this=exp.Column(this=exp.Identifier(this="age")),
        expression=exp.Literal(this=25)
    )
    
    row_data = [{"age": 25, "name": "John"}]
    metadata = {"table": ["users"]}
    
    filter_predicates = constraint_tree.capture_filter_predicates(
        "filter", "filter_1", filter_condition, row_data, metadata
    )
    
    # Add predicates to the tree
    for predicate_info in filter_predicates:
        constraint_tree.add_constraint_to_tree(
            constraint_tree.tree.root_constraint,
            predicate_info,
            row_data
        )
    
    # Example: Capture predicates from a join operator
    join_condition = exp.EQ(
        this=exp.Column(this=exp.Identifier(this="user_id")),
        expression=exp.Column(this=exp.Identifier(this="id"))
    )
    
    left_data = [{"user_id": 1, "name": "John"}]
    right_data = [{"id": 1, "email": "john@example.com"}]
    join_metadata = {"table": ["users", "profiles"]}
    
    join_predicates = constraint_tree.capture_join_predicates(
        "join", "join_1", join_condition, left_data, right_data, join_metadata
    )
    
    # Add join predicates to the tree
    for predicate_info in join_predicates:
        constraint_tree.add_constraint_to_tree(
            constraint_tree.tree.root_constraint,
            predicate_info,
            left_data + right_data
        )
    
    # Generate comprehensive report
    report = constraint_tree.generate_comprehensive_report()
    
    print("=== Constraint Tree Coverage Report ===")
    print(f"Overall Coverage: {report['coverage_ratio']:.2%}")
    print(f"Total Constraints: {report['total_constraints']}")
    print(f"Covered Constraints: {report['covered_constraints']}")
    
    print("\n=== Operator Coverage ===")
    for op_key, stats in report['operator_coverage'].items():
        print(f"{op_key}: {stats['coverage_ratio']:.2%}")
    
    print("\n=== Recommendations ===")
    for rec in report['recommendations']:
        print(f"- {rec}")
    
    # Find uncovered paths
    uncovered_paths = constraint_tree.find_uncovered_paths()
    print(f"\n=== Uncovered Paths: {len(uncovered_paths)} ===")
    
    # Suggest test cases
    test_cases = constraint_tree.suggest_test_cases(uncovered_paths)
    print(f"\n=== Suggested Test Cases: {len(test_cases)} ===")
    
    return constraint_tree, report

if __name__ == "__main__":
    constraint_tree, report = example_usage() 