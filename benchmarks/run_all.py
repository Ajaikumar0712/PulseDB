#!/usr/bin/env python3
"""
Master benchmark runner — runs all comparison benchmarks and prints a
unified summary table showing PulseDB vs PostgreSQL vs MongoDB vs Redis vs Qdrant.

Usage:
  # Quick run (100K rows, 50 concurrent)
  python run_all.py

  # Full run (1M rows, 1000 concurrent)
  python run_all.py --rows 1000000 --concurrency 1000

  # Only specific databases
  python run_all.py --dbs pulsedb postgres

  # Skip databases that aren't running
  python run_all.py --skip-errors

  # Output JSON for CI comparison
  python run_all.py --json results/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure benchmarks root is on sys.path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from common import BenchResult, print_table, save_json

COMPARE = ROOT / "compare"
sys.path.insert(0, str(COMPARE))


# ── Database runners ──────────────────────────────────────────────────────────

def run_pulsedb(rows: int, concurrency: int, vec_rows: int, out_dir: str) -> list[BenchResult]:
    import pulsedb_bench
    return pulsedb_bench.run_all(
        host="127.0.0.1", port=7878,
        rows=rows, concurrency=concurrency,
        vec_rows=vec_rows, out_dir=out_dir,
    )


def run_postgres(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    import postgres_bench
    return postgres_bench.run_all(rows=rows, concurrency=concurrency, out_dir=out_dir)


def run_mongodb(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    import mongodb_bench
    return mongodb_bench.run_all(rows=rows, concurrency=concurrency, out_dir=out_dir)


def run_redis(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    import redis_bench
    return redis_bench.run_all(rows=rows, concurrency=concurrency, out_dir=out_dir)


def run_qdrant(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    import qdrant_bench
    return qdrant_bench.run_all(
        rows=min(rows, 100_000), dims=128,
        concurrency=min(concurrency, 100), out_dir=out_dir,
    )


DB_RUNNERS = {
    "pulsedb":  run_pulsedb,
    "postgres": run_postgres,
    "mongodb":  run_mongodb,
    "redis":    run_redis,
    "qdrant":   run_qdrant,
}


# ── Comparison table ──────────────────────────────────────────────────────────

# Operations we care about for the cross-DB comparison
COMPARE_OPS = [
    ("INSERT",        ["INSERT (tx batch=1000)", "INSERT (executemany batch=1000)",
                       "insertMany batch=1000", "HSET pipeline batch=1000",
                       "INSERT single-row", "INSERT (single)"]),
    ("POINT LOOKUP",  ["GET WHERE id = N (point)", "SELECT WHERE id = N (PK)",
                       "find({id: N}) — indexed", "HGETALL bench:<id> (point)",
                       "retrieve by ID (point)"]),
    ("RANGE SCAN",    ["RANGE SCAN (10% of", "range_scan"]),
    ("FULL SCAN",     ["FULL SCAN (score > 0.5)", "SCAN + client filter (score > 0.5)"]),
    ("AGGREGATION",   ["GROUP BY + COUNT + AVG", "aggregate GROUP BY",
                       "GROUP BY active + COUNT"]),
    ("ORDER / TOP-N", ["ORDER BY score DESC LIMIT 100", "find.sort(score DESC)",
                       "ZREVRANGE TOP 100"]),
    ("FUZZY SEARCH",  ["FIND WHERE name", "FUZZY SEARCH", "TEXT SEARCH"]),
    ("VECTOR SEARCH", ["SIMILAR (HNSW", "search k=10", "VECTOR SEARCH"]),
    ("CONCURRENT",    ["CONCURRENT"]),
]


def find_result(results: list[BenchResult], op_patterns: list[str]) -> BenchResult | None:
    for r in results:
        for pat in op_patterns:
            if pat.lower() in r.operation.lower():
                return r
    return None


def print_comparison(all_results: dict[str, list[BenchResult]], rows: int) -> None:
    dbs = list(all_results.keys())
    col_w = max(len(d) for d in dbs) + 2

    print(f"\n{'=' * 90}")
    print(f"  CROSS-DATABASE COMPARISON — {rows:,} rows")
    print(f"  TPS = operations per second   p50/p99 = latency percentiles (ms)")
    print(f"{'=' * 90}")

    # Header
    op_w = 20
    header = f"{'Operation':<{op_w}}" + "".join(f"  {db:>{col_w}}" for db in dbs)
    print(header)
    print("-" * len(header))

    for op_label, patterns in COMPARE_OPS:
        row_parts = [f"{op_label:<{op_w}}"]
        for db in dbs:
            r = find_result(all_results.get(db, []), patterns)
            if r:
                row_parts.append(f"  {r.tps:>{col_w-2},.0f} TPS")
            else:
                row_parts.append(f"  {'—':>{col_w}}")
        print("".join(row_parts))

    print("-" * len(header))

    # Latency rows
    print(f"\n{'Operation':<{op_w}}" + "".join(f"  {db+' p50ms':>{col_w+4}}" for db in dbs))
    print("-" * (op_w + len(dbs) * (col_w + 6)))

    for op_label, patterns in COMPARE_OPS:
        row_parts = [f"{op_label:<{op_w}}"]
        for db in dbs:
            r = find_result(all_results.get(db, []), patterns)
            if r:
                row_parts.append(f"  {r.latency.p50_ms:>{col_w+3}.2f}ms")
            else:
                row_parts.append(f"  {'—':>{col_w+4}}")
        print("".join(row_parts))

    print()


def save_comparison(all_results: dict[str, list[BenchResult]], rows: int, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "comparison.json")
    data = {
        "rows": rows,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "databases": {
            db: [
                {
                    "operation": r.operation,
                    "tps": r.tps,
                    "p50_ms": r.latency.p50_ms,
                    "p95_ms": r.latency.p95_ms,
                    "p99_ms": r.latency.p99_ms,
                    "notes": r.notes,
                }
                for r in results
            ]
            for db, results in all_results.items()
        },
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Comparison JSON saved → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PulseDB comparison benchmark suite")
    parser.add_argument("--rows", type=int, default=100_000,
                        help="Dataset size. Use 1000000 for 1M, 10000000 for 10M. Default: 100,000")
    parser.add_argument("--concurrency", type=int, default=100,
                        help="Concurrent clients for TPS test. Default: 100")
    parser.add_argument("--vec-rows", type=int, default=10_000,
                        help="Rows for vector search (default: 10,000)")
    parser.add_argument("--dbs", nargs="+",
                        choices=list(DB_RUNNERS.keys()) + ["all"],
                        default=["all"],
                        help="Databases to benchmark (default: all)")
    parser.add_argument("--skip-errors", action="store_true",
                        help="Skip databases that fail to connect instead of exiting")
    parser.add_argument("--out-dir", default="results",
                        help="Directory for JSON output files")
    args = parser.parse_args()

    dbs_to_run = list(DB_RUNNERS.keys()) if "all" in args.dbs else args.dbs
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║        PulseDB Comparison Benchmark Suite                    ║
║  rows={args.rows:>12,}   concurrency={args.concurrency:<6}                ║
║  databases: {', '.join(dbs_to_run):<46}║
╚══════════════════════════════════════════════════════════════╝
    """)

    all_results: dict[str, list[BenchResult]] = {}
    wall_start = time.perf_counter()

    for db in dbs_to_run:
        runner = DB_RUNNERS[db]
        print(f"\n{'─' * 70}")
        print(f"  Running: {db.upper()}")
        print(f"{'─' * 70}")

        try:
            if db == "pulsedb":
                results = runner(args.rows, args.concurrency, args.vec_rows, args.out_dir)
            elif db == "qdrant":
                results = runner(args.rows, args.concurrency, args.out_dir)
            else:
                results = runner(args.rows, args.concurrency, args.out_dir)

            all_results[db] = results

        except Exception as e:
            if args.skip_errors:
                print(f"  SKIPPED {db}: {e}")
            else:
                print(f"\n  ERROR running {db}: {e}")
                print(f"  Use --skip-errors to continue past connection failures.")
                raise

    wall_elapsed = time.perf_counter() - wall_start

    if len(all_results) > 1:
        print_comparison(all_results, args.rows)
        save_comparison(all_results, args.rows, args.out_dir)

    print(f"\nTotal benchmark time: {wall_elapsed:.1f}s")
    print(f"Results in: {args.out_dir}/")


if __name__ == "__main__":
    main()
