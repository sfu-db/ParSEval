# DB Manager Module

The DB Manager module is ParSEval's central component for managing database connections, executing SQL statements, and handling database-specific operations across different SQL dialects. It provides a unified interface for working with SQLite, MySQL, and PostgreSQL databases.

## Overview

The DB Manager module handles:
- Database connection pooling and management
- SQL statement execution with timeout handling
- Database creation and schema management
- Dialect-specific SQL generation and identifier quoting
- Thread-safe connection management
- Resource cleanup and connection recycling

## Architecture

### Core Components

1. **`DBManager` Class** (Singleton):
   - Maintains a pool of SQLAlchemy engines keyed by connection URL
   - Handles database initialization and connection lifecycle
   - Provides thread-safe access to database connections
   - Implements connection cleanup and resource management

2. **`Connect` Class**:
   - Wrapper around SQLAlchemy engines for executing SQL
   - Provides methods for query execution, table operations, and schema inspection
   - Handles timeout protection (especially for SQLite)
   - Manages metadata reflection and invalidation

3. **Backend Providers** (`_SQLiteProvider`, `_MySQLProvider`, `_PostgresProvider`):
   - Dialect-specific implementations for database creation and engine configuration
   - Handle connection pooling strategies appropriate for each database type
   - Implement database existence checks and creation

### Key Features

- **Thread-Safe Singleton Pattern**: Ensures only one DBManager instance exists
- **Connection Pooling**: Efficient reuse of database connections
- **Dialect Abstraction**: Unified interface for SQLite, MySQL, and PostgreSQL
- **Timeout Protection**: Special handling for SQLite query timeouts
- **Automatic Database Creation**: Creates databases if they don't exist
- **Resource Cleanup**: Proper disposal of connections and engines
- **Metadata Management**: Automatic reflection and invalidation of table schemas

## Workflow

### 1. Connection Acquisition

When requesting a database connection:

```python
with DBManager().get_connection(
    connection_string="sqlite:///test.db",
    dialect="sqlite"
) as conn:
    # Use the connection here
    conn.execute("SELECT * FROM users")
```

The connection process:
1. Normalizes the connection string and validates dialect compatibility
2. Checks if the database needs to be created (for file-based databases)
3. Creates or retrieves a SQLAlchemy engine from the pool
4. Wraps the engine in a `Connect` object for SQL execution
5. Yields the connection for use in a context manager
6. Cleans up stale pools after use

### 2. SQL Execution

The `Connect.execute()` method handles:
- Parameterized queries to prevent SQL injection
- Flexible fetch options (all, one, random, or specific number of rows)
- Query timeout protection (especially important for SQLite)
- Proper transaction management via SQLAlchemy's context managers
- Error handling and logging

### 3. Database Operations

Available operations through the `Connect` interface:
- `create_tables(*ddls)`: Execute DDL statements to create tables
- `drop_table(table_name)`: Remove a table and invalidate metadata
- `clear_tables(*table_names)`: Delete all rows from specified tables
- `insert(stmt, data)`: Bulk insert data using parameterized queries
- `get_schema()`: Retrieve DDL for all tables in the database
- `get_table_rows(table_name)`: Fetch all rows from a specific table
- `export_database()`: Generate SQL statements to recreate the database

### 4. Dialect Handling

The module automatically handles dialect-specific differences:
- Identifier quoting (SQLite: `"identifier"`, MySQL: `` `identifier` ``, PostgreSQL: `"identifier"`)
- Database creation syntax
- Connection parameters and pooling strategies
- SQLGlot dialect mapping for SQL generation
- Timeout implementation variations

## Configuration

### Connection Parameters

The `get_connection` method accepts these parameters:

```python
DBManager().get_connection(
    connection_string: str,     # SQLAlchemy-compatible connection URL
    dialect: Literal["sqlite", "mysql", "postgres"],  # SQL dialect
    pool_size: int = 10,        # Number of connections to maintain
    max_overflow: int = 20,     # Additional connections allowed during peak
    pool_timeout: int = 15,     # Seconds to wait for connection from pool
    pool_recycle: int = 60,     # Seconds after which connections are recycled
    connect_timeout: int = 25,  # Seconds to wait for connection establishment
    create_if_missing: bool = True  # Whether to create database if absent
)
```

### Supported Connection String Formats

- **SQLite**: `sqlite:///path/to/database.db` or `sqlite:///:memory:`
- **MySQL**: `mysql://user:password@host:port/database`
- **PostgreSQL**: `postgres://user:password@host:port/database`

## Usage Examples

### Basic Query Execution

```python
from parseval.db_manager import DBManager

# Execute a simple query
with DBManager().get_connection("sqlite:///test.db", "sqlite") as conn:
    results = conn.execute("SELECT * FROM users WHERE age > ?", parameters=[18])
    for row in results:
        print(row)
```

### Bulk Insert Operations

```python
# Prepare data for insertion
users_data = [
    {"name": "Alice", "email": "alice@example.com", "age": 25},
    {"name": "Bob", "email": "bob@example.com", "age": 30},
    {"name": "Charlie", "email": "charlie@example.com", "age": 35}
]

# Execute bulk insert
with DBManager().get_connection("sqlite:///test.db", "sqlite") as conn:
    conn.insert(
        "INSERT INTO users (name, email, age) VALUES (:name, :email, :age)",
        users_data
    )
```

### Schema Inspection

```python
# Get current database schema
with DBManager().get_connection("sqlite:///test.db", "sqlite") as conn:
    schema_ddl = conn.get_schema()
    print(schema_ddl)
    
    # Get table metadata
    table_info = conn.metadata.tables['users']
    print(f"Table users has {len(table_info.columns)} columns")
```

### Transaction Management

```python
# The Connect context manager automatically handles transactions
with DBManager().get_connection("sqlite:///test.db", "sqlite") as conn:
    # Multiple operations in a single transaction
    conn.execute("INSERT INTO users (name) VALUES (:name)", parameters=[{"name": "Alice"}])
    conn.execute("INSERT INTO users (name) VALUES (:name)", parameters=[{"name": "Bob"}])
    # Both inserts are committed automatically when exiting the context
```

### Error Handling

```python
from sqlalchemy.exc import OperationalError

try:
    with DBManager().get_connection("sqlite:///test.db", "sqlite") as conn:
        results = conn.execute("SELECT * FROM nonexistent_table")
except OperationalError as e:
    print(f"Database error: {e}")
```

## Advanced Features

### Timeout Protection (SQLite-specific)

The module implements special timeout handling for SQLite queries using progress handlers:
- Queries that exceed the timeout threshold are interrupted
- Prevents hanging queries from blocking application execution
- Works with SQLAlchemy's execution context

### Connection Pooling Strategies

Different database types use appropriate pooling strategies:
- **SQLite**: Uses `NullPool` for file-based databases, `StaticPool` for in-memory
- **MySQL/PostgreSQL**: Uses standard `QueuePool` with configurable size and overflow

### Metadata Management

The module automatically reflects and caches database schema metadata:
- Metadata is invalidated after DDL operations (CREATE/DROP/ALTER)
- Subsequent schema queries use cached metadata for performance
- Manual invalidation available via `_invalidate_metadata()` method

### Resource Cleanup

Automatic cleanup mechanisms prevent resource leaks:
- Connections are returned to the pool after use
- Stale connections are periodically evicted based on idle time
- All engines are disposed of at program exit via atexit handler

## Best Practices

1. **Always Use Context Manters**: Use `with` statements to ensure proper connection cleanup
2. **Parameterize Queries**: Always use parameterized queries to prevent SQL injection
3. **Set Appropriate Timeouts**: Configure timeouts based on your query complexity and data volume
4. **Pool Sizing**: Adjust pool_size and max_overflow based on concurrent connection needs
5. **Dialect Specification**: Always specify the correct dialect for your database type
6. **Handle Exceptions**: Catch database-specific exceptions for robust error handling
7. **Memory Databases**: Use `sqlite:///:memory:` for temporary, isolated testing
8. **Connection Strings**: Use proper URL encoding for special characters in passwords

## Integration with Other Modules

The DB Manager is used throughout ParSEval:
- **Instance Module**: For persisting generated data to actual databases (`to_db` method)
- **Loader/Exporter**: For database operations in instance loading/exporting
- **Testing Framework**: For setting up and tearing down test databases
- **Query Generation**: For executing generated queries against target databases

## Extending the DB Manager

While designed to work out-of-the-box, the DB Manager can be extended by:
1. Adding new backend providers for additional database types
2. Customizing connection arguments for specific database drivers
3. Extending the `Connect` class with additional helper methods
4. Modifying the timeout or pooling strategies for specialized use cases
5. Adding support for additional SQLAlchemy features (events, listeners, etc.)

The modular design with clear separation between the manager, connection wrapper, and backend providers makes extension straightforward while maintaining thread safety and resource management guarantees.