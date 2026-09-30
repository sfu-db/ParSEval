#!/usr/bin/env python3
"""Measure direct SMT solving as a U-expression join chain grows.

The schema/query shape is reduced from multi-join cases in ``postgres.csv``.
Each size runs in a child process as a final wall-clock guard. The solver also
shares a deadline across encoding, solving, and model materialization.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from typing import Any


def _run(join_count: int, solver_timeout_ms: int, sender) -> None:
    from parseval.catalog import Catalog
    from parseval.coverage import next_paths, target_is_covered
    from parseval.instance import Instance
    from parseval.parser.query import lower_query
    from parseval.smt.solver import Solver
    from parseval.terms.arena import TermArena
    from parseval.uexpr import UExprCompiler

    started = time.monotonic()
    try:
        ddl = "; ".join(
            f"CREATE TABLE t{index}(id INT, next_id INT)"
            for index in range(join_count + 1)
        )
        joins = " ".join(
            f"JOIN t{index} ON t{index - 1}.next_id = t{index}.id"
            for index in range(1, join_count + 1)
        )
        catalog = Catalog.from_ddl(ddl, dialect="postgres")
        query = lower_query(f"SELECT t0.id FROM t0 {joins}", catalog)
        arena = TermArena(query.arena.context)
        root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
        target = next_paths(arena, root, Instance.empty(catalog))[0]
        sender.send(
            {
                "stage": "encoding",
                "join_count": join_count,
                "elapsed_s": round(time.monotonic() - started, 3),
            }
        )
        result = Solver(catalog, timeout_ms=solver_timeout_ms).solve(arena, target)
        sender.send(
            {
                "stage": "done",
                "join_count": join_count,
                "status": result.status.value,
                "support_classes": sum(count for _, count in result.statistics.support),
                "encoding_steps": result.statistics.encoding_steps,
                "circuit_nodes": result.statistics.circuit_nodes,
                "attempts": result.statistics.attempts,
                "reason": result.reason,
                "covered": result.instance is not None
                and target_is_covered(arena, result.instance, target),
                "elapsed_s": round(time.monotonic() - started, 3),
            }
        )
    except Exception as error:
        sender.send(
            {
                "stage": "error",
                "join_count": join_count,
                "error": type(error).__name__,
                "message": str(error),
                "elapsed_s": round(time.monotonic() - started, 3),
            }
        )
    finally:
        sender.close()


def benchmark(
    join_count: int,
    solver_timeout_ms: int,
    wall_timeout_s: float,
) -> dict[str, Any]:
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_run, args=(join_count, solver_timeout_ms, sender))
    process.start()
    sender.close()
    deadline = time.monotonic() + wall_timeout_s
    latest: dict[str, Any] = {"join_count": join_count, "stage": "startup"}
    while process.is_alive() and time.monotonic() < deadline:
        if receiver.poll(min(0.1, max(0.0, deadline - time.monotonic()))):
            try:
                latest = receiver.recv()
            except EOFError:
                break
    # Give a worker that just delivered its terminal message time to exit.
    process.join(timeout=0.2)
    while receiver.poll():
        try:
            latest = receiver.recv()
        except EOFError:
            break
    if process.is_alive():
        process.terminate()
        process.join()
        latest = {
            **latest,
            "stage": "wall_timeout",
            "wall_timeout_s": wall_timeout_s,
        }
    receiver.close()
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-joins", type=int, default=8)
    parser.add_argument("--solver-timeout-ms", type=int, default=1_000)
    parser.add_argument("--wall-timeout-s", type=float, default=10.0)
    args = parser.parse_args()
    if (
        args.max_joins < 1
        or args.solver_timeout_ms <= 0
        or args.wall_timeout_s <= 0
    ):
        parser.error("join count and timeouts must be positive")
    for join_count in range(1, args.max_joins + 1):
        print(
            json.dumps(
                benchmark(join_count, args.solver_timeout_ms, args.wall_timeout_s)
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
