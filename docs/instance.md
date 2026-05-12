# Instance Module

The Instance module is a core component of ParSEval responsible for managing database instances, generating schema-compliant data, and maintaining referential integrity. It serves as the bridge between schema definitions and actual data generation.

## Overview

The Instance module provides functionality to:
- Parse DDL (Data Definition Language) statements to build database schemas
- Generate synthetic data that adheres to schema constraints
- Maintain referential integrity between related tables
- Manage primary keys, foreign keys, unique constraints, and nullable columns
- Export generated data to actual databases

## Architecture

The Instance module consists of several key components:

### Core Components

1. **`Catalog` Class** (in `core.py`): 
   - Extends `sqlglot.schema.MappingSchema` to manage table schemas
   - Handles primary keys, foreign keys, and column constraints
   - Provides methods to check nullability and uniqueness of columns

2. **`Instance` Class** (in `core.py`):
   - Main class representing a database instance
   - Inherits from `Catalog` and adds data generation capabilities
   - Manages row creation, symbol tracking, and database synchronization

3. **Schema Processing** (in `schema.py`):
   - `build_schema_spec` function: Parses DDL and builds structured schema specifications
   - Helper functions for extracting primary keys, unique constraints, and foreign keys

4. **Data Export/Import** (in `exporter.py` and `loader.py`):
   - `InstanceExporter`: Converts instance data to SQL INSERT statements
   - `InstanceLoader`: Loads instance data into target databases

5. **Type Definitions** (in `types.py`):
   - Dataclasses for representing snapshots, batches, and operation results

## Workflow

The typical workflow for using the Instance module involves:

### 1. Initialization

```python
from parseval.instance import Instance

# Define DDL for your database schema
ddls = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    amount DECIMAL(10,2),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# Create an instance
instance = Instance(ddls=ddls, name="test_db", dialect="sqlite")
```

### 2. Schema Building

During initialization, the Instance:
1. Parses the DDL statements using SQLGlot
2. Builds a dependency graph to determine table creation order (parents before children)
3. Creates tables with their columns and data types
4. Adds primary keys, foreign keys, and constraints
5. Registers column domains for data generation

### 3. Data Generation

Data generation happens through the `create_row` and `create_rows` methods:

```python
# Create a single row with specific values
result = instance.create_row(
    table_name="users",
    values={"name": "John Doe", "email": "john@example.com"}
)

# Create multiple rows
concretes = {
    "users": {
        "name": ["Alice", "Bob", "Charlie"],
        "email": ["alice@example.com", "bob@example.com", "charlie@example.com"]
    }
}
results = instance.create_rows(concretes)
```

The data generation process:
1. Normalizes table and column names according to dialect
2. Checks for existing rows that match the provided values (to avoid duplicates when constraints apply)
3. Resolves foreign key references by creating parent rows when needed
4. Uses the Domain module to generate values for unspecified columns
5. Ensures unique constraints are not violated
6. Creates symbols representing the generated values for tracking

### 4. Referential Integrity Maintenance

The Instance automatically handles foreign key relationships:
- When creating a child row with a foreign key, if the referenced parent value doesn't exist, it creates the parent row
- For unique foreign keys, it ensures that each child references a distinct parent when possible
- It tracks which values have been used to maintain distribution realism

### 5. Data Retrieval

Generated data can be accessed through various methods:
- `get_rows(table_name)`: Get all rows for a table
- `get_row(table_name, index)`: Get a specific row by index
- `get_column_data(table_name, column_name)`: Get all values for a specific column

### 6. Persistence and Export

Data can be persisted to actual databases:
```python
# Export to SQLite database
instance.to_db("sqlite:///test.db")

# Get SQL INSERT statements without executing
sql_statements = instance.to_db("sqlite:///test.db", return_inserted=True)
```

The export process:
1. Creates a snapshot of the current instance state
2. Optionally truncates existing tables in the target database
3. Creates tables based on the DDL
4. Inserts all generated rows using parameterized queries

## Key Features

### Constraint Handling

The Instance module properly handles various database constraints:

- **Primary Keys**: Automatically generated when not provided, ensuring uniqueness
- **Foreign Keys**: Automatically resolves references by creating parent rows
- **Unique Constraints**: Prevents duplicate values in constrained columns
- **Not Null Constraints**: Ensures required columns receive values
- **Composite Constraints**: Handles multi-column unique keys and primary keys

### Symbol Management

Each generated value is associated with a symbol for tracking:
- Symbols track the relationship between generated values and their source columns
- Enables tracing of data lineage through foreign key relationships
- Facilitates debugging and analysis of generated datasets

### Domain Integration

The Instance leverages the Domain module for intelligent data generation:
- Uses column-specific generators based on data types
- Respects length, precision, and scale constraints
- Generates realistic distributions of values
- Handles special constraints like ENUM, regex patterns, etc.

### Thread Safety and Reset Capability

- Instances can be reset to their initial state while preserving the schema
- Useful for generating multiple independent datasets from the same schema
- Clean separation between schema definition and instance data

## Configuration

The Instance can be configured through its constructor:

```python
Instance(
    ddls: str,           # DDL statements defining the schema
    name: str,           # Name identifier for the instance
    dialect: str,        # SQL dialect (sqlite, mysql, postgresql, etc.)
    normalize: bool = True  # Whether to normalize identifiers
)
```

Additional configuration options are available through the underlying Domain and Builder systems.

## Usage Examples

### Basic Usage

```python
from parseval.instance import Instance

# Simple schema with two related tables
ddls = """
CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    department_id INTEGER,
    salary DECIMAL(10,2),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);
"""

# Create instance
instance = Instance(ddls=ddls, name="company", dialect="sqlite")

# Generate some departments
dept_result = instance.create_rows({
    "departments": {
        "name": ["Engineering", "Marketing", "Sales"]
    }
})

# Generate employees with automatic foreign key resolution
emp_result = instance.create_rows({
    "employees": {
        "name": ["Alice Smith", "Bob Johnson", "Carol Williams", "David Brown"],
        "salary": [75000, 65000, 80000, 72000]
        # department_id will be auto-generated to reference existing departments
    }
})

# Export to database
instance.to_db("sqlite:///company.db")
```

### Advanced Usage with Specific Values

```python
# Create a department with specific ID
dept_result = instance.create_row(
    table_name="departments",
    values={"id": 10, "name": "Human Resources"}
)

# Create employees that must reference the specific department
emp_result = instance.create_row(
    table_name="employees",
    values={
        "name": "Eve Davis",
        "department_id": 10,  # Will reference the HR department we created
        "salary": 68000
    }
)
```

### Checking Constraint Compliance

```python
# Check if a column can be null
is_nullable = instance.nullable("employees", "department_id")  # Returns True

# Check if a column has unique constraint
is_unique = instance.is_unique("departments", "name")  # Returns True

# Get primary key columns
pk_columns = instance.get_primary_key("employees")  # Returns {"id"}

# Get foreign key relationships
fks = instance.get_foreign_key("employees")  # Returns foreign key to departments
```

## Best Practices

1. **Schema Design**: Design your DDL with proper constraints to enable realistic data generation
2. **Batch Operations**: Use `create_rows` for generating multiple related rows efficiently
3. **Foreign Key Awareness**: When possible, provide explicit values for foreign keys to control relationships
4. **Reset Between Tests**: Call `instance.reset()` to clear data while preserving schema for test isolation
5. **Domain Knowledge**: Leverage the Domain module's capabilities by providing hints through column types and constraints
6. **Dialect Specifics**: Be aware of datatype differences between SQL dialects when designing schemas

## Error Handling

The Instance module raises specific exceptions for different error conditions:
- `UniqueConflictError`: When attempting to generate duplicate values in constrained columns
- `ForeignKeyResolutionError`: When unable to resolve foreign key references after multiple attempts
- `SchemaError`: When the provided DDL is invalid or incompatible

These exceptions inherit from base exceptions in the `parseval.states` module and can be caught and handled appropriately.

## Integration with Other Modules

The Instance module works closely with:
- **Domain Module**: For intelligent value generation based on column specifications
- **Planner Module**: For generating queries that operate on the generated data
- **Solver Module**: For validating and solving constraints during query generation
- **DBManager**: For actual database connections and operations

This integration enables ParSEval's core functionality of generating schema-compliant data and testing SQL engines against it.

## Extending the Instance

While designed to be used as-is, the Instance module can be extended by:
1. Subclassing the `Instance` class to override specific behaviors
2. Providing custom domain generators through the Domain module
3. Modifying the schema building process for specialized needs
4. Adding persistence layers for different storage backends

The modular design allows for flexibility while maintaining strong guarantees about data correctness and constraint compliance.