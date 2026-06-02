#!/usr/bin/env python3
"""
Redis comparison benchmark.

Requires:
  pip install redis
  Redis running on localhost:6379

Environment variables:
  REDIS_HOST (default: 127.0.0.1)
  REDIS_PORT (default: 6379)
  REDIS_DB   (default: 0)

Usage:
  python redis_bench.py
  python redis_bench.py --rows 1000000
  python redis_bench.py --rows 100000 --concurrency 100
"""

from __future__ import annotations

import argparse
import json
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
    import redis
    from redis import Redis
except ImportError:
    print("Install redis:  pip install redis")
    sys.exit(1)


# ── Connection helpers ────────────────────────────────────────────────────────

def connect() -> Redis:
    return Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )


def setup(r: Redis) -> None:
    r.flushdb()


def make_row(i: int) -> str:
    return json.dumps({
        "id": i, "name": f"user_{i}",
        "score": round(i * 0.001, 6), "active": i % 2 == 0,
    })


def insert_rows(r: Redis, rows: int, batch: int = 1000) -> None:
    for start in range(0, rows, batch):
        pipe = r.pipeline(transaction=False)
        end = min(start + batch, rows)
        for i in range(start, end):
            pipe.hset(f"bench:{i}", mapping={
                "id": i, "name": f"user_{i}",
                "score": round(i * 0.001, 6), "active": i % 2 == 0,
            })
        pipe.execute()


# ── Benchmarks ────────────────────────────────────────────────────────────────

def bench_insert(rows: int, batch: int = 1000) -> BenchResult:
    r = connect()
    setup(r)
    t0 = time.perf_counter()
    for start in range(0, rows, batch):
        pipe = r.pipeline(transaction=False)
        end = min(start + batch, rows)
        for i in range(start, end):
            pipe.hset(f"bench:{i}", mapping={
                "id": i, "name": f"user_{i}",
                "score": round(i * 0.001, 6), "active": i % 2 == 0,
            })
        pipe.execute()
    elapsed = time.perf_counter() - t0
    r.close()
    tps = rows / elapsed
    lat = LatencyStats(elapsed * 1000 / (rows / batch), 0, 0,
                       elapsed * 1000 / (rows / batch), 0, elapsed * 1000)
    return BenchResult("Redis", f"HSET pipeline batch={batch}", rows, tps, lat)


def bench_point_lookup(rows: int, iterations: int = 500) -> BenchResult:
    r = connect()
    setup(r)
    insert_rows(r, rows)
    target = rows // 2
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        r.hgetall(f"bench:{target}")
        times.append(time.perf_counter() - t0)
    r.close()
    lat = latency_stats(times)
    return BenchResult("Redis", "HGETALL bench:<id> (point)", rows,
                       1 / (lat.mean_ms / 1000), lat)


def bench_pipeline_reads(rows: int, batch: int = 100, iterations: int = 100) -> BenchResult:
    import random
    r = connect()
    setup(r)
    insert_rows(r, rows)
    times = []
    for _ in range(iterations):
        keys = [f"bench:{random.randint(0, rows - 1)}" for _ in range(batch)]
        t0 = time.perf_counter()
        pipe = r.pipeline(transaction=False)
        for k in keys:
            pipe.hgetall(k)
        pipe.execute()
        times.append(time.perf_counter() - t0)
    r.close()
    lat = latency_stats(times)
    return BenchResult("Redis", f"Pipeline GET {batch} keys", rows,
                       batch / (lat.mean_ms / 1000), lat,
                       notes=f"{batch} keys per pipeline")


def bench_scan_filter(rows: int, iterations: int = 5) -> BenchResult:
    """Full scan via SCAN + HGETALL + client-side filter."""
    r = connect()
    setup(r)
    insert_rows(r, rows)
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        cursor = "0"
        while True:
            cursor, keys = r.scan(cursor=cursor, match="bench:*", count=1000)
            if keys:
                pipe = r.pipeline(transaction=False)
                for k in keys:
                    pipe.hget(k, "score")
                pipe.execute()
            if cursor == "0" or cursor == 0:
                break
        times.append(time.perf_counter() - t0)
    r.close()
    lat = latency_stats(times)
    return BenchResult("Redis", "SCAN + client filter (score > 0.5)", rows,
                       rows / (lat.mean_ms / 1000), lat,
                       notes="no server-side filter")


def bench_sorted_set_range(rows: int, iterations: int = 100) -> BenchResult:
    """Sorted set for ORDER BY simulation."""
    r = connect()
    setup(r)
    insert_rows(r, rows)
    for start in range(0, rows, 5000):
        pipe = r.pipeline(transaction=False)
        end = min(start + 5000, rows)
        for i in range(start, end):
            pipe.zadd("bench_score", {str(i): i * 0.001})
        pipe.execute()
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        r.zrevrange("bench_score", 0, 99, withscores=True)
        times.append(time.perf_counter() - t0)
    r.close()
    lat = latency_stats(times)
    return BenchResult("Redis", "ZREVRANGE TOP 100 (sorted set)", rows,
                       100 / (lat.mean_ms / 1000), lat,
                       notes="sorted set, score index")


def bench_concurrent(rows: int, concurrency: int) -> BenchResult:
    r = connect()
    setup(r)
    insert_rows(r, rows)
    r.close()
    target = rows // 2
    calls_per_thread = 100
    all_times: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker():
        c = connect()
        barrier.wait()
        local = []
        for _ in range(calls_per_thread):
            t0 = time.perf_counter()
            c.hgetall(f"bench:{target}")
            local.append(time.perf_counter() - t0)
        c.close()
        with lock:
            all_times.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    wall_start = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    wall_elapsed = time.perf_counter() - wall_start
    tps = (concurrency * calls_per_thread) / wall_elapsed
    lat = latency_stats(all_times)
    return BenchResult("Redis", f"CONCURRENT {concurrency} clients (HGETALL)", rows,
                       tps, lat, notes=f"{concurrency * calls_per_thread:,} total ops")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    r = connect()
    info = r.connection_pool.connection_kwargs
    r.close()
    print(f"\nRedis Benchmarks — {info['host']}:{info['port']}  rows={rows:,}  concurrency={concurrency}")
    print("=" * 70)
    results = []

    def run(label: str, fn):
        print(f"  {label}...", end=" ", flush=True)
        t0 = time.perf_counter()
        r = fn()
        elapsed = time.perf_counter() - t0
        results.append(r)
        print(f"{r.tps:,.0f} TPS  p50={r.latency.p50_ms:.2f}ms  [{elapsed:.1f}s]")

    run("HSET pipeline insert",  lambda: bench_insert(rows))
    run("HGETALL point lookup",  lambda: bench_point_lookup(rows))
    run("Pipeline 100 reads",    lambda: bench_pipeline_reads(rows))
    run("SCAN + client filter",  lambda: bench_scan_filter(min(rows, 50_000)))
    run("ZREVRANGE TOP 100",     lambda: bench_sorted_set_range(rows))
    run(f"CONCURRENT {concurrency}", lambda: bench_concurrent(rows, concurrency))

    print_table("Redis", results)
    save_json("Redis", results, out_dir)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redis benchmark")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--out-dir", default="results")
    args = parser.parse_args()
    run_all(args.rows, args.concurrency, args.out_dir)
