#!/usr/bin/env python3
"""Run every original postgres.csv statement with isolated resource budgets.

Every input receives a JSONL record. Identical SQL/schema pairs share a run;
records identify reuse explicitly. Errors and timeouts are never counted as
successful generation. Generated instances are saved as SQLite databases;
each is inspected for table row counts and queried to check for non-empty
results. PostgreSQL replay is enabled with --postgres-dsn.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from benchmark_postgres_coverage import run_with_timeout


def source_digest():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for directory in ('src/parseval', 'scripts'):
        for path in sorted((root / directory).rglob('*.py')):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def replay_sqlite(path, query, dialect, timeout_s):
    """Inspect a saved instance and check for a row using SQLite itself."""
    import sqlglot
    from sqlglot import exp

    result = {'tables': {}, 'nonempty': None}
    deadline = time.monotonic() + timeout_s
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        connection.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT GLOB 'sqlite_*' ORDER BY name"
        )]
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            result['tables'][name] = connection.execute(f'SELECT COUNT(*) FROM {quoted}').fetchone()[0]
        result['input_rows'] = sum(result['tables'].values())

        statements = sqlglot.parse(query, read=dialect)
        if len(statements) != 1 or statements[0] is None:
            raise ValueError('SQLite replay requires one SQL statement')
        tree = statements[0]
        # The exporter drops schemas unless table names collide. Resolve both
        # forms against the actual saved tables, preserving aliases and CTEs.
        exported = {name.casefold(): name for name in names}
        for table in tree.find_all(exp.Table):
            if not table.db and not table.catalog:
                continue
            qualified = '.'.join(part.name for part in table.parts)
            name = exported.get(qualified.casefold(), exported.get(table.name.casefold()))
            if name is not None:
                table.set('this', exp.to_identifier(name, quoted=True))
                table.set('db', None)
                table.set('catalog', None)
        result['sql'] = tree.sql(dialect='sqlite', unsupported_level=sqlglot.ErrorLevel.RAISE)
        cursor = connection.execute(result['sql'])
        if cursor.description is None:
            raise ValueError('SQLite replay requires a query returning rows')
        result['nonempty'] = cursor.fetchone() is not None
        result['status'] = 'ok'
    except Exception as error:
        timed_out = isinstance(error, sqlite3.OperationalError) and 'interrupted' in str(error).lower()
        result['status'] = 'timeout' if timed_out else 'error'
        result['error'] = {'type': type(error).__name__, 'message': str(error)[:500]}
    finally:
        if connection is not None:
            connection.close()
    return result


def run_audit(row, query, solver_ms, wall_s, sqlite_dir, sqlite_wall_s, postgres_dsn):
    result = run_with_timeout(row, query, solver_ms, wall_s,
                              sqlite_dir=sqlite_dir, postgres_dsn=postgres_dsn)
    result['sqlite_dir'] = str(sqlite_dir)
    checks = []
    # Also inspect instances saved before a generation timeout or error.
    for relative in result.get('sqlite', []):
        check = replay_sqlite(sqlite_dir / relative, row[query], row['dialect'], sqlite_wall_s)
        checks.append({'path': relative, **check})
    result['sqlite_checks'] = checks
    result['sqlite_nonempty'] = (
        True if any(check['nonempty'] is True for check in checks)
        else False if checks and all(check['status'] == 'ok' for check in checks)
        else None
    )
    result['sqlite_replay_status'] = (
        'nonempty' if result['sqlite_nonempty'] is True
        else 'empty' if result['sqlite_nonempty'] is False
        else 'error' if any(check['status'] == 'error' for check in checks)
        else 'timeout' if checks else 'not_run'
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/postgres.csv'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--wall-s', type=float, default=15)
    parser.add_argument('--solver-ms', type=int, default=1000)
    parser.add_argument('--sqlite-dir', type=Path,
                        help='export root (default: <output stem>_sqlite beside the JSONL)')
    parser.add_argument('--sqlite-wall-s', type=float, default=5,
                        help='time limit for inspecting and querying each saved database (default: 5)')
    parser.add_argument('--postgres-dsn')
    args = parser.parse_args(argv)
    if min(args.workers, args.wall_s, args.solver_ms, args.sqlite_wall_s) <= 0:
        parser.error('time budgets and workers must be positive')
    with args.input.open() as stream:
        rows = list(csv.DictReader(stream))
    jobs = {}
    for row in rows:
        for query in ('q1', 'q2'):
            jobs.setdefault((row['schema_ddl'], row[query], row['dialect']), []).append((row, query))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sqlite_root = args.sqlite_dir or args.output.parent / f'{args.output.stem}_sqlite'
    sqlite_root.mkdir(parents=True, exist_ok=True)
    # Separate invocations so timeout recovery cannot pick up older exports.
    sqlite_dir = Path(tempfile.mkdtemp(prefix='run-', dir=sqlite_root)).resolve()
    initial_source = source_digest()
    counts = Counter()
    with args.output.open('w') as output, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_audit, cases[0][0], cases[0][1], args.solver_ms,
                               args.wall_s, sqlite_dir, args.sqlite_wall_s,
                               args.postgres_dsn): cases
                   for cases in jobs.values()}
        for future in as_completed(futures):
            cases = futures[future]
            result = future.result()
            for offset, (row, query) in enumerate(cases):
                record = {**result, 'dbid': row['dbid'], 'index': row['index'], 'query': query}
                if offset:
                    record['reused_from'] = [cases[0][0]['dbid'], cases[0][0]['index'], cases[0][1]]
                status = ('error' if 'error' in record else 'timeout' if record.get('status') == 'timeout'
                          else 'unsupported' if record.get('unsupported_generation')
                          else 'productive' if record.get('productive')
                          else 'populated_no_output_change' if record.get('populated') else 'empty_only')
                record['audit_status'] = status
                counts[status] += 1
                counts[f"sqlite_{record['sqlite_replay_status']}"] += 1
                counts['statements'] += 1
                output.write(json.dumps(record, default=str) + '\n')
            output.flush()
            if counts['statements'] % 50 < len(cases):
                print(json.dumps(dict(counts)), flush=True)
    summary = {'summary': dict(counts), 'unique_runs': len(jobs),
               'source_sha256': initial_source, 'source_unchanged': initial_source == source_digest(),
               'dataset_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
               'budgets': {'workers': args.workers, 'wall_s': args.wall_s,
                           'solver_ms': args.solver_ms,
                           'sqlite_wall_s': args.sqlite_wall_s},
               'sqlite_dir': str(sqlite_dir),
               'postgres_replay': args.postgres_dsn is not None}
    args.output.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
