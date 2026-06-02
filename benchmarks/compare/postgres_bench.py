#!/usr/bin/env python3
"""
PostgreSQL comparison benchmark.

Requires:
  pip install psycopg2-binary
  PostgreSQL running on localhost:5432 (or set env vars)

Environment variables:
  PGHOST     (default: 127.0.0.1)
  PGPORT     (default: 5432)
  PGDATABASE (default: benchmark)
  PGUSER     (default: postgres)
  PGPASSWORD (default: postgres)

Usage:
  python postgres_bench.py
  python postgres_bench.py --rows 1000000
  python postgres_bench.py --rows 10000 --concurrency 100
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
    import psycopg2
    import psycopg2.pool
except ImportError:
    print("Install psycopg2:  pip install psycopg2-binary")
    sys.exit(1)


# ── Connection helpers ────────────────────────────────────────────────────────

def dsn() -> dict:
    return dict(
        host     = os.getenv("PGHOST", "127.0.0.1"),
        port     = int(os.getenv("PGPORT", "5432")),
        dbname   = os.getenv("PGDATABASE", "benchmark"),
        user     = os.getenv("PGUSER", "postgres"),
        password = os.getenv("PGPASSWORD", "postgres"),
    )


def connect():
    return psycopg2.connect(**dsn())


def setup_table(conn, with_index: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bench")
        cur.execute("""
            CREATE TABLE bench (
                id     BIGINT PRIMARY KEY,
                name   TEXT,
                score  DOUBLE PRECISION,
                active BOOLEAN
            )
        """)
        if with_index:
            cur.execute("CREATE INDEX bench_score_idx ON bench(score)")
    conn.commit()


def insert_rows(conn, rows: int, batch: int = 1000) -> None:
    with conn.cursor() as cur:
        for start in range(0, rows, batch):
            end = min(start + batch, rows)
            cur.executemany(
                "INSERT INTO bench (id, name, score, active) VALUES (%s, %s, %s, %s)",
                [
                    (i, f"user_{i}", i * 0.001, i % 2 == 0)
                    for i in range(start, end)
                ],
            )
    conn.commit()


# ── Benchmarks ────────────────────────────────────────────────────────────────

def bench_insert(rows: int, batch: int = 1000) -> BenchResult:
    conn = connect()
    setup_table(conn, with_index=False)

    t0 = time.perf_counter()
    with conn.cursor() as cur:
        for start in range(0, rows, batch):
            end = min(start + batch, rows)
            cur.executemany(
                "INSERT INTO bench VALUES (%s, %s, %s, %s)",
                [(i, f"user_{i}", i * 0.001, i % 2 == 0) for i in range(start, end)],
            )
    conn.commit()
    elapsed = time.perf_counter() - t0
    conn.close()

    tps = rows / elapsed
    lat = LatencyStats(elapsed * 1000 / (rows / batch), 0, 0,
                       elapsed * 1000 / (rows / batch), 0, elapsed * 1000)
    return BenchResult("PostgreSQL", f"INSERT (executemany batch={batch})", rows, tps, lat)


def bench_point_lookup(rows: int, iterations: int = 200) -> BenchResult:
    conn = connect()
    setup_table(conn)
    insert_rows(conn, rows)
    target = rows // 2

    times = []
    with conn.cursor() as cur:
        for _ in range(iterations):
            t0 = time.perf_counter()
            cur.execute("SELECT * FROM bench WHERE id = %s", (target,))
            cur.fetchall()
            times.append(time.perf_counter() - t0)
    conn.close()

    lat = latency_stats(times)
    return BenchResult("PostgreSQL", "SELECT WHERE id = N (PK)", rows,
                       1 / (lat.mean_ms / 1000), lat)


def bench_range_scan(rows: int, iterations: int = 50) -> BenchResult:
    conn = connect()
    setup_table(conn)
    insert_rows(conn, rows)
    lo, hi = rows // 10, rows // 10 * 2

    times = []
    with conn.cursor() as cur:
        for _ in range(iterations):
            t0 = time.perf_counter()
            cur.execute("SELECT * FROM bench WHERE id >= %s AND id < %s", (lo, hi))
            cur.fetchall()
            times.append(time.perf_counter() - t0)
    conn.close()

    lat = latency_stats(times)
    result_rows = hi - lo
    return BenchResult("PostgreSQL", f"RANGE SCAN (10% of {rows:,})", rows,
                       result_rows / (lat.mean_ms / 1000), lat,
                       notes=f"{result_rows:,} rows returned")


def bench_full_scan(rows: int, iterations: int = 20) -> BenchResult:
    conn = connect()
    setup_table(conn)
    insert_rows(conn, rows)

    times = []
    with conn.cursor() as cur:
        for _ in range(iterations):
            t0 = time.perf_counter()
            cur.execute("SELECT * FROM bench WHERE score > 0.5")
            cur.fetchall()
            times.append(time.perf_counter() - t0)
    conn.close()

    lat = latency_stats(times)
    return BenchResult("PostgreSQL", "FULL SCAN (score > 0.5)", rows,
                       (rows / 2) / (lat.mean_ms / 1000), lat,
                       notes=f"~{rows // 2:,} rows matched")


def bench_aggregation(rows: int, iterations: int = 30) -> BenchResult:
    conn = connect()
    setup_table(conn)
    insert_rows(conn, rows)

    times = []
    with conn.cursor() as cur:
        for _ in range(iterations):
            t0 = time.perf_counter()
            cur.execute("SELECT active, COUNT(*), AVG(score) FROM bench GROUP BY active")
            cur.fetchall()
            times.append(time.perf_counter() - t0)
    conn.close()

    lat = latency_stats(times)
    return BenchResult("PostgreSQL", "GROUP BY + COUNT + AVG(score)", rows,
                       rows / (lat.mean_ms / 1000), lat)


def bench_order_limit(rows: int, iterations: int = 50) -> BenchResult:
    conn = connect()
    setup_table(conn)
    insert_rows(conn, rows)

    times = []
    with conn.cursor() as cur:
        for _ in range(iterations):
            t0 = time.perf_counter()
            cur.execute("SELECT * FROM bench ORDER BY score DESC LIMIT 100")
            cur.fetchall()
            times.append(time.perf_counter() - t0)
    conn.close()

    lat = latency_stats(times)
    return BenchResult("PostgreSQL", "ORDER BY score DESC LIMIT 100", rows,
                       100 / (lat.mean_ms / 1000), lat)


def bench_fuzzy_search(rows: int, iterations: int = 50) -> BenchResult:
    """Uses pg_trgm extension for trigram similarity (closest to PulseDB's ~ operator)."""
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    conn.commit()
    setup_table(conn)
    insert_rows(conn, rows)
    with conn.cursor() as cur:
        cur.execute("CREATE INDEX bench_trgm ON bench USING gin(name gin_trgm_ops)")
    conn.commit()

    times = []
    with conn.cursor() as cur:
        for _ in range(iterations):
            t0 = time.perf_counter()
            cur.execute(
                "SELECT *, similarity(name, %s) AS score FROM bench "
                "WHERE name %% %s ORDER BY score DESC LIMIT 20",
                ("user_5", "user_5"),
            )
            cur.fetchall()
            times.append(time.perf_counter() - t0)
    conn.close()

    lat = latency_stats(times)
    return BenchResult("PostgreSQL", 'FUZZY SEARCH pg_trgm ~ "user_5"', rows,
                       20 / (lat.mean_ms / 1000), lat,
                       notes="pg_trgm GIN index")


def bench_concurrent(rows: int, concurrency: int) -> BenchResult:
    """N threads, each using their own connection, doing point lookups."""
    conn = connect()
    setup_table(conn)
    insert_rows(conn, rows)
    conn.close()

    target = rows // 2
    calls_per_thread = 50
    all_times: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker():
        c = connect()
        with c.cursor() as cur:
            barrier.wait()
            local = []
            for _ in range(calls_per_thread):
                t0 = time.perf_counter()
                cur.execute("SELECT * FROM bench WHERE id = %s", (target,))
                cur.fetchall()
                local.append(time.perf_counter() - t0)
        c.close()
        with lock:
            all_times.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_elapsed = time.perf_counter() - wall_start

    total_ops = concurrency * calls_per_thread
    tps = total_ops / wall_elapsed
    lat = latency_stats(all_times)
    return BenchResult("PostgreSQL", f"CONCURRENT {concurrency} clients (point lookup)", rows,
                       tps, lat, notes=f"{total_ops:,} total ops")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all(rows: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    info = dsn()
    print(f"\nPostgreSQL Benchmarks — {info['host']}:{info['port']}/{info['dbname']}  "
          f"rows={rows:,}  concurrency={concurrency}")
    print("=" * 70)

    results = []

    def run(label: str, fn):
        print(f"  {label}...", end=" ", flush=True)
        t0 = time.perf_counter()
        r = fn()
        elapsed = time.perf_counter() - t0
        results.append(r)
        print(f"{r.tps:,.0f} TPS  p50={r.latency.p50_ms:.2f}ms  [{elapsed:.1f}s]")

    run("INSERT batch",        lambda: bench_insert(rows))
    run("POINT LOOKUP",        lambda: bench_point_lookup(rows))
    run("RANGE SCAN (10%)",    lambda: bench_range_scan(rows))
    run("FULL SCAN",           lambda: bench_full_scan(rows))
    run("AGGREGATION",         lambda: bench_aggregation(rows))
    run("ORDER BY LIMIT 100",  lambda: bench_order_limit(rows))
    run("FUZZY SEARCH (trgm)", lambda: bench_fuzzy_search(min(rows, 100_000)))
    run(f"CONCURRENT {concurrency}", lambda: bench_concurrent(rows, concurrency))

    print_table("PostgreSQL", results)
    save_json("PostgreSQL", results, out_dir)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PostgreSQL benchmark")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--out-dir", default="results")
    args = parser.parse_args()
    run_all(args.rows, args.concurrency, args.out_dir)
