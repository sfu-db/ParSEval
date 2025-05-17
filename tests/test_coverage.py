#!/usr/bin/env python3
"""
Example script demonstrating how to use the enhanced Coverage class and CoverageAnalyzer.
"""

import os
import sys
import logging
from typing import Dict, Any

# Add the parent directory to the path so we can import the src package
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.runtime.coverage import Coverage, CoverageType
from src.runtime.coverage_analyzer import CoverageAnalyzer

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def simulate_query_execution() -> Coverage:
    """
    Simulates a query execution and returns a Coverage object with execution data.
    
    Returns:
        A Coverage object containing simulated execution data
    """
    # Create a new Coverage object
    coverage = Coverage()
    
    # Simulate a scan operation
    coverage.traceit(
        operator_key="scan",
        operator_id="1",
        constraint_id="1",
        event=CoverageType.PATH,
        label=True,
        details={
            "table_name": "users",
            "row_count": 100
        }
    )
    
    # Simulate a filter operation
    coverage.traceit(
        operator_key="filter",
        operator_id="2",
        constraint_id="predicate_1",
        event=CoverageType.PREDICATE,
        label=True,
        details={
            "condition": "age > 18",
            "satisfied": True
        },
        parent_operator_id="1"
    )
    
    coverage.traceit(
        operator_key="filter",
        operator_id="2",
        constraint_id="predicate_2",
        event=CoverageType.PREDICATE,
        label=False,
        details={
            "condition": "age > 18",
            "satisfied": False
        },
        parent_operator_id="1"
    )
    
    # Simulate a project operation
    coverage.traceit(
        operator_key="project",
        operator_id="3",
        constraint_id="1",
        event=CoverageType.PATH,
        label=True,
        details={
            "projected_columns": ["id", "name", "age"]
        },
        parent_operator_id="2"
    )
    
    # Simulate an aggregate operation
    coverage.traceit(
        operator_key="aggregate",
        operator_id="4",
        constraint_id="size",
        event=CoverageType.GROUP,
        label=True,
        details={
            "group_index": 0,
            "group_size": 5,
            "has_multiple_rows": True
        },
        parent_operator_id="3"
    )
    
    coverage.traceit(
        operator_key="aggregate",
        operator_id="4",
        constraint_id="count",
        event=CoverageType.GROUP,
        label=True,
        details={
            "group_count": 3,
            "has_multiple_groups": True
        },
        parent_operator_id="3"
    )
    
    return coverage

def main():
    """Main function to demonstrate the Coverage class and CoverageAnalyzer."""
    logger.info("Simulating query execution...")
    coverage = simulate_query_execution()
    
    logger.info("Generating coverage report...")
    report = CoverageAnalyzer.generate_coverage_report(coverage)
    
    # Print some basic statistics
    logger.info(f"Total coverage entries: {report['total_entries']}")
    logger.info(f"Coverage by type: {report['coverage_by_type']}")
    
    # Save the report to a file
    report_file = "coverage_report.json"
    logger.info(f"Saving coverage report to {report_file}...")
    CoverageAnalyzer.save_report(report, report_file)
    
    # Save the coverage data to a file
    coverage_file = "coverage_data.json"
    logger.info(f"Saving coverage data to {coverage_file}...")
    coverage.save_to_file(coverage_file)
    
    # Load the coverage data from the file
    logger.info(f"Loading coverage data from {coverage_file}...")
    loaded_coverage = Coverage.load_from_file(coverage_file)
    
    # Verify that the loaded data is the same as the original
    logger.info(f"Loaded coverage entries: {len(loaded_coverage.trace())}")
    
    # Find constraint violations
    violations = CoverageAnalyzer.find_constraint_violations(coverage)
    logger.info(f"Found {len(violations)} constraint violations")
    
    # Create visualizations
    try:
        logger.info("Creating operator hierarchy visualization...")
        CoverageAnalyzer.visualize_operator_hierarchy(coverage, "operator_hierarchy.png")
        
        logger.info("Creating coverage by type visualization...")
        CoverageAnalyzer.visualize_coverage_by_type(coverage, "coverage_by_type.png")
        
        logger.info("Visualizations saved to operator_hierarchy.png and coverage_by_type.png")
    except ImportError as e:
        logger.warning(f"Could not create visualizations: {e}")
        logger.warning("Make sure matplotlib and networkx are installed to create visualizations.")
    
    logger.info("Done!")

if __name__ == "__main__":
    main() 