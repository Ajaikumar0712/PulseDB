#!/usr/bin/env python3
"""
SQLite comparison benchmark — mirrors the PulseDB Criterion suite.
Requires: Python 3.8+, no extra packages (sqlite3 is stdlib).

Usage:
    python sqlite_bench.py
    python sqlite_bench.py --rows 100000
"""

import sqlite3
import time
import argparse
import statistics

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
        print(f"  {label:<40} {elapsed*1000:>10.2f} ms   {rate:>12,.0f} rows/s")
    else:
        print(f"  {label:<40} {elapsed*1000:>10.2f} ms")

# ── Benchmark cases ───────────────────────────────────────────────────────

def run_benchmarks(rows: int):
    print(f"\nSQLite Benchmarks (rows={rows:,})\n" + "=" * 65)

    # ── INSERT ─────────────────────────────────────────────────────────
    print("\nINSERT throughput:")

    def insert_no_index():
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE bench (id INT, name TEXT, score REAL, active INT)")
        conn.executemany(
            "INSERT INTO bench VALUES (?,?,?,?)",
            [(i, f"user_{i}", i * 0.001, i % 2) for i in range(rows)]
        )
        conn.commit()
        conn.close()

    bench(f"INSERT {rows:,} rows (no index)", insert_no_index, rows)

    def insert_with_index():
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE bench (id INT, name TEXT, score REAL, active INT)")
        conn.execute("CREATE INDEX idx_id ON bench(id)")
        conn.executemany(
            "INSERT INTO bench VALUES (?,?,?,?)",
            [(i, f"user_{i}", i * 0.001, i % 2) for i in range(rows)]
        )
        conn.commit()
        conn.close()

    bench(f"INSERT {rows:,} rows (with index)", insert_with_index, rows)

    # Shared DB for read benchmarks
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bench (id INT, name TEXT, score REAL, active INT)")
    conn.execute("CREATE INDEX idx_id ON bench(id)")
    conn.executemany(
        "INSERT INTO bench VALUES (?,?,?,?)",
        [(i, f"user_{i}", i * 0.001, i % 2) for i in range(rows)]
    )
    conn.commit()

    # ── POINT LOOKUP ───────────────────────────────────────────────────
    print("\nPoint lookup:")
    target = rows // 2

    def point_lookup():
        list(conn.execute("SELECT * FROM bench WHERE id = ?", (target,)))

    bench(f"SELECT WHERE id = {target} (indexed)", point_lookup)

    # ── RANGE SCAN ─────────────────────────────────────────────────────
    print("\nRange scan:")
    lo, hi = rows // 10, rows // 10 * 2

    def range_scan():
        list(conn.execute("SELECT * FROM bench WHERE id > ? AND id < ?", (lo, hi)))

    bench(f"SELECT WHERE id BETWEEN {lo} AND {hi}", range_scan)

    # ── FULL SCAN ──────────────────────────────────────────────────────
    print("\nFull scan with filter:")

    def full_scan():
        list(conn.execute("SELECT * FROM bench WHERE score > 0.5"))

    bench("SELECT WHERE score > 0.5 (no index)", full_scan, rows)

    # ── AGGREGATION ────────────────────────────────────────────────────
    print("\nAggregation:")

    def aggregation():
        list(conn.execute(
            "SELECT active, COUNT(*), AVG(score) FROM bench GROUP BY active"
        ))

    bench("GROUP BY active + COUNT + AVG", aggregation)

    # ── LIKE (text search) ─────────────────────────────────────────────
    print("\nText search (LIKE):")

    def text_search():
        list(conn.execute("SELECT * FROM bench WHERE name LIKE '%user_5%'"))

    bench("SELECT WHERE name LIKE '%user_5%'", text_search)

    # ── TRANSACTION ────────────────────────────────────────────────────
    print("\nTransaction throughput:")

    def tx_10_inserts():
        c = conn.cursor()
        for i in range(10):
            c.execute("INSERT INTO bench VALUES (?,?,?,?)", (9_000_000 + i, f"tx_{i}", 0.1, 1))
        conn.commit()

    bench("BEGIN + 10 INSERTs + COMMIT", tx_10_inserts)

    conn.close()
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite comparison benchmarks")
    parser.add_argument("--rows", type=int, default=10_000)
    args = parser.parse_args()
    run_benchmarks(args.rows)
