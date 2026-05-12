# ParSEval Documentation

ParSEval is a sophisticated tool for validating SQL parsers and database engines through automated query generation and smart data synthesis.

## Core Features

- **Query Generation**: Generate complex SQL queries covering various clauses (SELECT, JOIN, WHERE, GROUP BY, HAVING, etc.).
- **Differential Testing**: Compare the results of the same query across different database engines or parser versions.
- **Smart Data Generation**: Utilize the [Domain Module](domain.md) to generate schema-valid data that satisfies complex constraints and referential integrity.
- **Dialect Support**: Built-in support for multiple SQL dialects including PostgreSQL, MySQL, and SQLite.

## Getting Started

### Installation

ParSEval uses Poetry for dependency management.

```bash
# Install dependencies
poetry install
```

### Basic Usage

(Add basic usage examples here if available in main.py)

---

## Technical Guides

- [Domain Module](domain.md): Deep dive into the data generation engine.
- [Instance Module](instance.md): Comprehensive guide to database instance management and data generation.
- [DB Manager](db_manager.md): Guide to database connection management and SQL operations.
- [Developer Guide](dev.md): Instructions for setting up the development environment.