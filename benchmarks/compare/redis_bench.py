#!/usr/bin/env python3
"""
Redis comparison benchmark — mirrors the PulseDB Criterion suite.
Uses Redis Hash structures (HSET/HGETALL) as the closest equivalent
to PulseDB's typed rows.

Requires:
    pip install redis
    Redis server running on localhost:6379

Usage:
    python redis_bench.py
    python redis_bench.py --rows 10000 --host 127.0.0.1 --port 6379
"""

import time
import statistics
import argparse

try:
    import redis
except ImportError:
    print("Install redis-py:  pip install redis")
    raise

# ── Helpers ───────────────────────────────────────────────────────────────

def timer(fn, iterations=3):
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)

def bench(label, fn, rows=None, iterations=3):
    elapsed = timer(fn, iterations)
    if rows:
        rate = rows / elapsed
        print(f"  {label:<45} {elapsed*1000:>10.2f} ms   {rate:>12,.0f} rows/s")
    else:
        print(f"  {label:<45} {elapsed*1000:>10.2f} ms")

# ── Benchmark cases ───────────────────────────────────────────────────────

def run_benchmarks(rows: int, host: str, port: int):
    r = redis.Redis(host=host, port=port, decode_responses=True)
    r.ping()
    print(f"\nRedis Benchmarks (rows={rows:,}, {host}:{port})\n" + "=" * 70)

    KEY_PREFIX = "bench:pulsedb:"
    SET_KEY    = "bench:pulsedb:ids"

    def cleanup():
        keys = r.keys(f"{KEY_PREFIX}*")
        if keys:
            r.delete(*keys)
        r.delete(SET_KEY)

    # ── INSERT ─────────────────────────────────────────────────────────
    print("\nINSERT throughput (HSET per row):")

    def insert_pipeline():
        cleanup()
        pipe = r.pipeline(transaction=False)
        for i in range(rows):
            key = f"{KEY_PREFIX}{i}"
            pipe.hset(key, mapping={
                "id":     i,
                "name":   f"user_{i}",
                "score":  f"{i * 0.001:.4f}",
                "active": int(i % 2 == 0),
            })
            pipe.sadd(SET_KEY, i)
        pipe.execute()

    bench(f"HSET + SADD {rows:,} rows (pipeline)", insert_pipeline, rows)

    # Populate for read benchmarks
    insert_pipeline()

    # ── POINT LOOKUP ───────────────────────────────────────────────────
    print("\nPoint lookup:")
    target = rows // 2

    def point_lookup():
        r.hgetall(f"{KEY_PREFIX}{target}")

    bench(f"HGETALL key:{target}", point_lookup)

    # ── RANGE SCAN (Redis has no native range on hash fields) ──────────
    print("\nRange scan (SCAN + HGETALL — no native index):")

    def range_scan():
        lo, hi = rows // 10, rows // 10 * 2
        results = []
        for key in r.scan_iter(f"{KEY_PREFIX}*"):
            row = r.hgetall(key)
            if row:
                rid = int(row.get("id", -1))
                if lo < rid < hi:
                    results.append(row)
        return results

    bench(f"SCAN + filter id IN [{rows//10},{rows//10*2}]", range_scan)

    # ── PIPELINE GET N KEYS ────────────────────────────────────────────
    print("\nBulk read (pipeline HGETALL for first 1000 keys):")

    def bulk_get():
        pipe = r.pipeline(transaction=False)
        for i in range(min(1_000, rows)):
            pipe.hgetall(f"{KEY_PREFIX}{i}")
        pipe.execute()

    bench("HGETALL ×1000 (pipeline)", bulk_get)

    # ── TRANSACTION (MULTI/EXEC) ───────────────────────────────────────
    print("\nTransaction throughput:")

    def tx_10_inserts():
        pipe = r.pipeline(transaction=True)
        for i in range(10):
            key = f"{KEY_PREFIX}tx:{i}"
            pipe.hset(key, mapping={"id": i, "name": f"tx_{i}", "score": 0.1, "active": 1})
        pipe.execute()

    bench("MULTI + 10 HSET + EXEC", tx_10_inserts)

    # ── SET MEMBERSHIP ─────────────────────────────────────────────────
    print("\nSet membership (SISMEMBER):")

    def set_member():
        r.sismember(SET_KEY, target)

    bench(f"SISMEMBER id={target}", set_member)

    cleanup()
    r.close()
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redis comparison benchmarks")
    parser.add_argument("--rows",  type=int, default=10_000)
    parser.add_argument("--host",  default="127.0.0.1")
    parser.add_argument("--port",  type=int, default=6379)
    args = parser.parse_args()
    run_benchmarks(args.rows, args.host, args.port)
