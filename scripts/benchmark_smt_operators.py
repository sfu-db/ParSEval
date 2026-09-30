#!/usr/bin/env python3
"""Direct U-semiring workloads; only Solver.solve is timed (JSONL output)."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import time
from dataclasses import asdict

import z3

from parseval.catalog import Catalog
from parseval.coverage.model import CoverageSite, CoverageTarget, WitnessedObligation
from parseval.smt.solver import Solver, SolveStatus
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.sorts import RowSort
from parseval.uexpr.observation import WeightCondition
from parseval.uexpr.witness import UnitWitnessPlan


CASES = (
    "sum_union", "product_counts", "join_chain", "exact_join", "squash_union",
    "correlated_exists", "correlated_absence", "nested_absence", "null_indicator",
    "support_diversity", "shared_dag", "contradiction", "squash_impossible",
)


def build_case(name: str, size: int):
    """Build closed IR directly, with explicit obligations and known outcomes.

    Size is branch/relation count, nesting depth, or required value diversity.
    DDL only creates a typed catalog; no SQL query compiler or path scheduler
    participates. Each relation has one nullable integer column and bag weights.
    """
    if name not in CASES or size < 1:
        raise ValueError("unknown case or nonpositive size")
    catalog = Catalog.from_ddl(
        ";".join(f"CREATE TABLE r{i}(x INT)" for i in range(size + 1)),
        dialect="postgres",
    )
    arena = TermArena(catalog.context)
    b = IRBuilder(arena)
    relations = tuple(catalog.context.relations())
    schemas = [spec.schema for _, spec in relations]
    bases = [b.base(relation) for relation, _ in relations]
    sql_type = catalog.context.schema(schemas[0]).fields[0].sql_type

    def count(i):
        return b.sum(RowSort(schemas[i]), lambda row: b.at(bases[i], row))

    def matching(i, outer):
        return b.sum(RowSort(schemas[i]), lambda row: b.mul(
            b.at(bases[i], row),
            b.indicator(b.eq3(b.field(outer, 0), b.field(row, 0))),
        ))

    def chain(i, outer=None):
        def body(row):
            factors = [b.at(bases[i], row)]
            if outer is not None:
                factors.append(b.indicator(b.eq3(b.field(outer, 0), b.field(row, 0))))
            if i < size:
                factors.append(chain(i + 1, row))
            return b.mul(*factors)
        return b.sum(RowSort(schemas[i]), body)

    def absent(i, outer):
        def body(row):
            factors = [b.at(bases[i], row),
                       b.indicator(b.eq3(b.field(outer, 0), b.field(row, 0)))]
            if i < size:
                factors.append(absent(i + 1, row))
            return b.mul(*factors)
        return b.unot(b.sum(RowSort(schemas[i]), body))

    conditions = []
    expected = SolveStatus.SAT

    def require(term, minimum=1, maximum=None):
        conditions.append(WeightCondition(b.finish(term), minimum, maximum))

    if name == "sum_union":
        term = b.add(*(count(i) for i in range(size)))
        for i in range(size):
            require(count(i), 2, 2)
        require(term, 2 * size, 2 * size)
    elif name == "product_counts":
        term = b.mul(*(count(i) for i in range(size)))
        for i in range(size):
            require(count(i), 2, 2)
        require(term, 2 ** size, 2 ** size)
    elif name in ("join_chain", "exact_join"):
        term = chain(0)
        if name == "exact_join":
            for i in range(size + 1):
                require(count(i), 2, 2)
            require(term, 2 ** (size + 1), 2 ** (size + 1))
        else:
            require(term)
    elif name in ("squash_union", "squash_impossible"):
        term = b.squash(b.add(*(count(i) for i in range(size))))
        require(term, 2 if name == "squash_impossible" else 1,
                2 if name == "squash_impossible" else 1)
        if name == "squash_impossible":
            expected = SolveStatus.BOUNDED_UNSAT
        else:
            for i in range(size):
                require(count(i), 3, 3)
    elif name in ("correlated_exists", "correlated_absence"):
        def body(row):
            checks = [matching(i, row) for i in range(1, size + 1)]
            guard = b.squash if name == "correlated_exists" else b.unot
            return b.mul(b.at(bases[0], row), *(guard(check) for check in checks))
        term = b.sum(RowSort(schemas[0]), body)
        require(term, 2, 2)
        # Absence must find different values, rather than simply empty tables.
        for i in range(1, size + 1):
            require(count(i), 2, 2)
    elif name == "nested_absence":
        term = b.sum(RowSort(schemas[0]), lambda row: b.mul(
            b.at(bases[0], row), absent(1, row)))
        require(term)
        for i in range(size + 1):
            require(count(i), 2, 2)
    elif name == "null_indicator":
        null = b.null(sql_type)
        # SQL [NOT(x = NULL)] is always zero; UNot([x = NULL]) is one.
        term = b.sum(RowSort(schemas[0]), lambda row: b.mul(
            b.at(bases[0], row),
            b.unot(b.indicator(b.eq3(b.field(row, 0), null))),
        ))
        rejected = b.sum(RowSort(schemas[0]), lambda row: b.mul(
            b.at(bases[0], row),
            b.indicator(b.not3(b.eq3(b.field(row, 0), null))),
        ))
        require(count(0), size, size)
        require(term, size, size)
        require(rejected, 0, 0)
    elif name == "support_diversity":
        term = count(0)
        for value in range(size):
            row = b.row(schemas[0], (b.literal(value, sql_type),))
            require(b.at(bases[0], row), 1, 1)
        require(term, size, size)
    elif name == "shared_dag":
        term = count(0)
        require(term, 2, 2)
        for _ in range(size):
            term = b.squash(b.add(term, term))
        require(term, 1, 1)
    else:  # A positive count and its absence cannot both hold.
        term = b.add(*(count(i) for i in range(size)))
        require(term)
        require(b.unot(term))
        expected = SolveStatus.BOUNDED_UNSAT
    root = b.finish(term)
    target = CoverageTarget(
        f"{name}:{size}", CoverageSite(root, ()),
        WitnessedObligation(UnitWitnessPlan(), tuple(conditions)), name,
    )
    return catalog, arena, target, expected


def run_case(name, size, *, timeout_ms=2000, max_support=8,
             max_encoding_steps=100_000, max_rows=10_000, minimize=False,
             progress=None):
    catalog, arena, target, expected = build_case(name, size)
    solver = Solver(catalog, timeout_ms=timeout_ms, max_support=max_support,
                    max_encoding_steps=max_encoding_steps, max_rows=max_rows,
                    minimize=minimize)
    if progress is not None:
        progress({"case": name, "size": size, "stage": "solve"})
    started = time.perf_counter()
    result = solver.solve(arena, target)
    elapsed = time.perf_counter() - started
    statistics = asdict(result.statistics)
    statistics["support"] = [(relation.value, count) for relation, count in result.statistics.support]
    return {
        "case": name, "size": size, "stage": "done",
        "expected": expected.value, "status": result.status.value,
        "matches_expected": result.status is expected, "reason": result.reason,
        "solve_wall_seconds": elapsed, **statistics,
    }


def _worker(sender, name, size, options):
    try:
        sender.send(run_case(name, size, progress=sender.send, **options))
    except Exception as error:
        sender.send({"case": name, "size": size, "stage": "error",
                     "error": type(error).__name__, "reason": str(error)})
    finally:
        sender.close()


def benchmark(name, size, wall_timeout_s, **options):
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, name, size, options))
    process.start()
    sender.close()
    deadline = time.monotonic() + wall_timeout_s
    latest = {"case": name, "size": size, "stage": "prepare"}
    try:
        while receiver.poll(max(0, deadline - time.monotonic())):
            try:
                latest = receiver.recv()
            except EOFError:
                return {**latest, "last_stage": latest["stage"], "stage": "worker_exit"}
            if latest["stage"] in ("done", "error"):
                return latest
        return {**latest, "last_stage": latest["stage"], "stage": "wall_timeout"}
    finally:
        process.join(timeout=0.1)
        if process.is_alive():
            process.terminate()
            process.join()
        receiver.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--sizes", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=2000)
    parser.add_argument("--wall-timeout-s", type=float, default=10)
    parser.add_argument("--max-support", type=int, default=8)
    parser.add_argument("--max-encoding-steps", type=int, default=100_000)
    parser.add_argument("--max-rows", type=int, default=10_000)
    parser.add_argument("--minimize", action="store_true")
    args = parser.parse_args()
    if min(*args.sizes, args.repeat, args.timeout_ms, args.wall_timeout_s,
           args.max_support, args.max_encoding_steps, args.max_rows) <= 0:
        parser.error("sizes, repetitions, and limits must be positive")
    options = {key: getattr(args, key) for key in (
        "timeout_ms", "max_support", "max_encoding_steps", "max_rows", "minimize")}
    for name in args.cases:
        for size in args.sizes:
            for repeat in range(args.repeat):
                result = benchmark(name, size, args.wall_timeout_s, **options)
                print(json.dumps({**result, "repeat": repeat, "limits": options,
                                  "python_version": platform.python_version(),
                                  "z3_version": z3.get_version_string(),
                                  "wall_timeout_s": args.wall_timeout_s}), flush=True)


if __name__ == "__main__":
    main()
