#!/usr/bin/env python3
"""
MongoDB comparison benchmark.

Requires:
  pip install pymongo
  MongoDB running on localhost:27017

Environment variables:
  MONGO_URI (default: mongodb://localhost:27017)

Usage:
  python mongodb_bench.py
  python mongodb_bench.py --rows 1000000
  python mongodb_bench.py --rows 100000 --concurrency 100
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from common import (
    BenchResult, LatencyStats, latency_stats,
    print_table, save_json,
)

try:
    import pymongo
    from pymongo import MongoClient, ASCENDING, DESCENDING
    from pymongo.errors import BulkWriteError
except ImportError:
    print("Install pymongo:  pip install pymongo")
    sys.exit(1)


# ── Connection helpers ────────────────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = "pulsedb_bench"
COL_NAME  = "bench"


def get_collection():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return client[DB_NAME][COL_NAME]


def setup_collection(col, with_index: bool = True) -> None:
    col.drop()
    if with_index:
        col.create_index([("id", ASCENDING)], unique=True)
        col.create_index([("score", ASCENDING)])


def insert_rows(col, rows: int, batch: int = 1000) -> None:
    for start in range(0, rows, batch):
        end = min(start + batch, rows)
        docs = [
            {"id": i, "name": f"user_{i}", "score": i * 0.001, "active": i % 2 == 0}
            for i in range(start, end)
        ]
        col.insert_many(docs, ordered=False)


# ── Benchmarks ────────────────────────────────────────────────────────────────

def bench_insert(rows: int, batch: int = 1000) -> BenchResult:
    col = get_collection()
    setup_collection(col, with_index=False)

    t0 = time.perf_counter()
    for start in range(0, rows, batch):
        end = min(start + batch, rows)
        docs = [
            {"id": i, "name": f"user_{i}", "score": i * 0.001, "active": i % 2 == 0}
            for i in range(start, end)
        ]
        col.insert_many(docs, ordered=False)
    elapsed = time.perf_counter() - t0

    tps = rows / elapsed
    lat = LatencyStats(elapsed * 1000 / (rows / batch), 0, 0,
                       elapsed * 1000 / (rows / batch), 0, elapsed * 1000)
    return BenchResult("MongoDB", f"insertMany batch={batch}", rows, tps, lat)


def bench_point_lookup(rows: int, iterations: int = 200) -> BenchResult:
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)
    target = rows // 2

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        list(col.find({"id": target}))
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("MongoDB", "find({id: N}) — indexed", rows,
                       1 / (lat.mean_ms / 1000), lat)


def bench_range_scan(rows: int, iterations: int = 50) -> BenchResult:
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)
    lo, hi = rows // 10, rows // 10 * 2

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        list(col.find({"id": {"$gte": lo, "$lt": hi}}))
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    result_rows = hi - lo
    return BenchResult("MongoDB", f"RANGE SCAN (10% of {rows:,})", rows,
                       result_rows / (lat.mean_ms / 1000), lat,
                       notes=f"{result_rows:,} docs returned")


def bench_full_scan(rows: int, iterations: int = 20) -> BenchResult:
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        list(col.find({"score": {"$gt": 0.5}}))
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("MongoDB", "FULL SCAN (score > 0.5)", rows,
                       (rows / 2) / (lat.mean_ms / 1000), lat,
                       notes=f"~{rows // 2:,} docs matched")


def bench_aggregation(rows: int, iterations: int = 30) -> BenchResult:
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)

    pipeline = [
        {"$group": {
            "_id": "$active",
            "count": {"$sum": 1},
            "avg_score": {"$avg": "$score"},
        }}
    ]

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        list(col.aggregate(pipeline))
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("MongoDB", "aggregate GROUP BY active + COUNT + AVG", rows,
                       rows / (lat.mean_ms / 1000), lat)


def bench_order_limit(rows: int, iterations: int = 50) -> BenchResult:
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        list(col.find({}).sort("score", DESCENDING).limit(100))
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("MongoDB", "find.sort(score DESC).limit(100)", rows,
                       100 / (lat.mean_ms / 1000), lat)


def bench_text_search(rows: int, iterations: int = 50) -> BenchResult:
    """MongoDB regex text search (closest to PulseDB's ~ operator)."""
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)
    col.create_index([("name", pymongo.TEXT)])

    import re
    pattern = re.compile("user_5", re.IGNORECASE)

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        list(col.find({"name": {"$regex": pattern}}).limit(20))
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("MongoDB", 'REGEX search ~ "user_5"', rows,
                       20 / (lat.mean_ms / 1000), lat,
                       notes="regex scan (no trigram)")


def bench_concurrent(rows: int, concurrency: int) -> BenchResult:
    col = get_collection()
    setup_collection(col)
    insert_rows(col, rows)

    target = rows // 2
    calls_per_thread = 50
    all_times: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker():
        c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        collection = c[DB_NAME][COL_NAME]
        barrier.wait()
        local = []
        for _ in range(calls_per_thread):
            t0 = time.perf_counter()
            list(collection.find({"id": target}))
            local.append(time.perf_counter() - t0)
        c.close()
        with lock:
            all_times.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    wall_start = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    wall_elapsed = time.perf_counter() - wall_start

    total_ops = concurrency * calls_per_thread
    tps = total_ops / wall_elapsed
    lat = latency_stats(all_times)
    return BenchResult("MongoDB", f"CONCURRENT {concurrency} clients (find by id)", rows,
                       tps, lat, notes=f"{total_ops:,} total ops")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    print(f"\nMongoDB Benchmarks — {MONGO_URI}  rows={rows:,}  concurrency={concurrency}")
    print("=" * 70)

    results = []

    def run(label: str, fn):
        print(f"  {label}...", end=" ", flush=True)
        t0 = time.perf_counter()
        r = fn()
        elapsed = time.perf_counter() - t0
        results.append(r)
        print(f"{r.tps:,.0f} TPS  p50={r.latency.p50_ms:.2f}ms  [{elapsed:.1f}s]")

    run("INSERT batch",       lambda: bench_insert(rows))
    run("POINT LOOKUP",       lambda: bench_point_lookup(rows))
    run("RANGE SCAN (10%)",   lambda: bench_range_scan(rows))
    run("FULL SCAN",          lambda: bench_full_scan(rows))
    run("AGGREGATION",        lambda: bench_aggregation(rows))
    run("ORDER BY LIMIT 100", lambda: bench_order_limit(rows))
    run("TEXT SEARCH (regex)",lambda: bench_text_search(min(rows, 100_000)))
    run(f"CONCURRENT {concurrency}", lambda: bench_concurrent(rows, concurrency))

    print_table("MongoDB", results)
    save_json("MongoDB", results, out_dir)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MongoDB benchmark")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--out-dir", default="results")
    args = parser.parse_args()
    run_all(args.rows, args.concurrency, args.out_dir)
