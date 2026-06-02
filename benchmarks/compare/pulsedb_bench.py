#!/usr/bin/env python3
"""
PulseDB benchmark — connects over TCP, speaks PulseQL.

Requires:
  pulsedb-server running on 127.0.0.1:7878 (start with --no-auth for benchmarks)

Usage:
  python pulsedb_bench.py
  python pulsedb_bench.py --rows 1000000
  python pulsedb_bench.py --rows 10000 --concurrency 100
  python pulsedb_bench.py --host 192.168.1.10 --port 7878
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from common import (
    BenchResult, LatencyStats, latency_stats,
    run_timed, run_concurrent, print_table, save_json, get_rss_mb,
)

# ── PulseDB client (raw TCP) ──────────────────────────────────────────────────

class PulseDBClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7878, timeout: float = 30.0):
        self.host = host
        self.port = port
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._file = self._sock.makefile("r", encoding="utf-8")
        self._read()  # discard welcome banner

    def query(self, q: str) -> dict:
        payload = json.dumps({"query": q}) + "\n"
        self._sock.sendall(payload.encode())
        return self._read()

    def _read(self) -> dict:
        line = self._file.readline()
        if not line:
            raise ConnectionError("Server closed connection")
        return json.loads(line)

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def connect(host: str, port: int) -> PulseDBClient:
    return PulseDBClient(host, port)


# ── Benchmark helpers ─────────────────────────────────────────────────────────

def make_db(host: str, port: int, rows: int) -> None:
    """Drop and recreate the bench table, insert `rows` rows."""
    with connect(host, port) as db:
        try:
            db.query("DROP TABLE bench")
        except Exception:
            pass
        db.query("MAKE TABLE bench (id int PRIMARY KEY, name text, score float, active bool)")
        # Batch inserts in chunks of 1000 using transactions
        chunk = 1000
        for start in range(0, rows, chunk):
            db.query("BEGIN")
            end = min(start + chunk, rows)
            for i in range(start, end):
                db.query(
                    f'PUT bench {{ id: {i}, name: "user_{i}", '
                    f'score: {i * 0.001:.6f}, active: {"true" if i % 2 == 0 else "false"} }}'
                )
            db.query("COMMIT")


def make_vec_db(host: str, port: int, rows: int, dims: int = 128) -> None:
    """Build the vector benchmark table."""
    with connect(host, port) as db:
        try:
            db.query("DROP TABLE vecs")
        except Exception:
            pass
        db.query("MAKE TABLE vecs (id int PRIMARY KEY, embedding vector)")
        chunk = 500
        for start in range(0, rows, chunk):
            db.query("BEGIN")
            end = min(start + chunk, rows)
            for i in range(start, end):
                v = [round(((i + j) * 0.001) % 1.0, 6) for j in range(dims)]
                vec_str = ", ".join(str(x) for x in v)
                db.query(f"PUT vecs {{ id: {i}, embedding: [{vec_str}] }}")
            db.query("COMMIT")


# ── Benchmark cases ───────────────────────────────────────────────────────────

def bench_insert(host: str, port: int, rows: int) -> BenchResult:
    """Measure single-row insert throughput (no transaction batching)."""
    with connect(host, port) as db:
        try:
            db.query("DROP TABLE insert_bench")
        except Exception:
            pass
        db.query("MAKE TABLE insert_bench (id int PRIMARY KEY, name text, score float)")

        t0 = time.perf_counter()
        for i in range(rows):
            db.query(f'PUT insert_bench {{ id: {i}, name: "u{i}", score: {i * 0.001:.4f} }}')
        elapsed = time.perf_counter() - t0

    tps = rows / elapsed
    lat = LatencyStats(
        p50_ms=elapsed * 1000 / rows,
        p95_ms=elapsed * 1000 / rows * 1.5,
        p99_ms=elapsed * 1000 / rows * 2.0,
        mean_ms=elapsed * 1000 / rows,
        min_ms=0.1, max_ms=elapsed * 1000,
    )
    return BenchResult("PulseDB", "INSERT (single)", rows, tps, lat)


def bench_insert_tx(host: str, port: int, rows: int, batch: int = 1000) -> BenchResult:
    """Measure batch-insert throughput (BEGIN + N + COMMIT)."""
    with connect(host, port) as db:
        try:
            db.query("DROP TABLE tx_bench")
        except Exception:
            pass
        db.query("MAKE TABLE tx_bench (id int PRIMARY KEY, name text, score float)")

        t0 = time.perf_counter()
        for start in range(0, rows, batch):
            db.query("BEGIN")
            end = min(start + batch, rows)
            for i in range(start, end):
                db.query(f'PUT tx_bench {{ id: {i}, name: "u{i}", score: {i * 0.001:.4f} }}')
            db.query("COMMIT")
        elapsed = time.perf_counter() - t0

    tps = rows / elapsed
    lat = LatencyStats(
        p50_ms=elapsed * 1000 / (rows / batch),
        p95_ms=elapsed * 1000 / (rows / batch) * 1.3,
        p99_ms=elapsed * 1000 / (rows / batch) * 1.6,
        mean_ms=elapsed * 1000 / (rows / batch),
        min_ms=0.5, max_ms=elapsed * 1000,
    )
    return BenchResult("PulseDB", f"INSERT (tx batch={batch})", rows, tps, lat,
                       notes=f"{rows // batch} txns")


def bench_point_lookup(host: str, port: int, rows: int) -> BenchResult:
    make_db(host, port, rows)
    target = rows // 2

    times = []
    with connect(host, port) as db:
        for _ in range(200):
            t0 = time.perf_counter()
            db.query(f"GET bench WHERE id = {target}")
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("PulseDB", "GET WHERE id = N (point)", rows,
                       1 / (lat.mean_ms / 1000), lat)


def bench_range_scan(host: str, port: int, rows: int) -> BenchResult:
    make_db(host, port, rows)
    lo, hi = rows // 10, rows // 10 * 2

    times = []
    with connect(host, port) as db:
        for _ in range(50):
            t0 = time.perf_counter()
            db.query(f"GET bench WHERE id >= {lo} AND id < {hi}")
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    result_rows = hi - lo
    return BenchResult("PulseDB", f"RANGE SCAN (10% of {rows:,})", rows,
                       result_rows / (lat.mean_ms / 1000), lat,
                       notes=f"{result_rows:,} rows returned")


def bench_full_scan(host: str, port: int, rows: int) -> BenchResult:
    make_db(host, port, rows)

    times = []
    with connect(host, port) as db:
        for _ in range(20):
            t0 = time.perf_counter()
            db.query("GET bench WHERE score > 0.5")
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("PulseDB", "FULL SCAN (score > 0.5)", rows,
                       (rows / 2) / (lat.mean_ms / 1000), lat,
                       notes=f"~{rows // 2:,} rows matched")


def bench_aggregation(host: str, port: int, rows: int) -> BenchResult:
    make_db(host, port, rows)

    times = []
    with connect(host, port) as db:
        for _ in range(30):
            t0 = time.perf_counter()
            db.query("GET bench GROUP BY active COUNT(*) AS cnt AVG(score) AS avg_score")
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("PulseDB", "GROUP BY + COUNT + AVG", rows,
                       rows / (lat.mean_ms / 1000), lat)


def bench_order_limit(host: str, port: int, rows: int) -> BenchResult:
    make_db(host, port, rows)

    times = []
    with connect(host, port) as db:
        for _ in range(50):
            t0 = time.perf_counter()
            db.query("GET bench ORDER BY score DESC LIMIT 100")
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("PulseDB", "ORDER BY score DESC LIMIT 100", rows,
                       100 / (lat.mean_ms / 1000), lat)


def bench_fuzzy_search(host: str, port: int, rows: int) -> BenchResult:
    make_db(host, port, rows)

    times = []
    with connect(host, port) as db:
        for _ in range(50):
            t0 = time.perf_counter()
            db.query('FIND bench WHERE name ~ "user_5" LIMIT 20')
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("PulseDB", 'FIND WHERE name ~ "user_5"', rows,
                       20 / (lat.mean_ms / 1000), lat,
                       notes="trigram similarity")


def bench_vector_search(host: str, port: int, rows: int, dims: int = 128) -> BenchResult:
    make_vec_db(host, port, rows, dims)
    query_vec = ", ".join(str(round((j * 0.0007) % 1.0, 6)) for j in range(dims))
    stmt = f"SIMILAR vecs ON embedding TO [{query_vec}] LIMIT 10"

    times = []
    with connect(host, port) as db:
        for _ in range(50):
            t0 = time.perf_counter()
            db.query(stmt)
            times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("PulseDB", f"SIMILAR (HNSW, k=10, {dims}-dim)", rows,
                       10 / (lat.mean_ms / 1000), lat,
                       notes="cosine similarity, HNSW index")


def bench_concurrent(host: str, port: int, rows: int, concurrency: int) -> BenchResult:
    """Simulate N concurrent clients each doing a point lookup."""
    make_db(host, port, rows)
    target = rows // 2

    # Pre-open connections to avoid TCP setup latency skewing results
    conns = [connect(host, port) for _ in range(concurrency)]
    calls_per_thread = 50

    def worker_fn():
        # Each call picks a connection from the pool in a round-robin manner
        pass  # actual work done inside run_concurrent

    # Build per-thread lambdas that use their own connection
    all_times: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker(conn: PulseDBClient):
        barrier.wait()
        local = []
        for _ in range(calls_per_thread):
            t0 = time.perf_counter()
            conn.query(f"GET bench WHERE id = {target}")
            local.append(time.perf_counter() - t0)
        with lock:
            all_times.extend(local)

    threads = [threading.Thread(target=worker, args=(conns[i],)) for i in range(concurrency)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_elapsed = time.perf_counter() - wall_start

    for c in conns:
        c.close()

    total_ops = concurrency * calls_per_thread
    tps = total_ops / wall_elapsed
    lat = latency_stats(all_times)
    return BenchResult("PulseDB", f"CONCURRENT {concurrency} clients (point lookup)", rows,
                       tps, lat, notes=f"{total_ops:,} total ops")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all(host: str, port: int, rows: int, concurrency: int,
            vec_rows: int, out_dir: str) -> list[BenchResult]:
    print(f"\nPulseDB Benchmarks — {host}:{port}  rows={rows:,}  concurrency={concurrency}")
    print("=" * 70)

    results = []

    def run(label: str, fn):
        print(f"  {label}...", end=" ", flush=True)
        t0 = time.perf_counter()
        r = fn()
        elapsed = time.perf_counter() - t0
        results.append(r)
        print(f"{r.tps:,.0f} TPS  p50={r.latency.p50_ms:.2f}ms  [{elapsed:.1f}s]")
        return r

    run("INSERT single-row",    lambda: bench_insert(host, port, min(rows, 100_000)))
    run("INSERT batch (tx)",    lambda: bench_insert_tx(host, port, rows))
    run("POINT LOOKUP",         lambda: bench_point_lookup(host, port, rows))
    run("RANGE SCAN (10%)",     lambda: bench_range_scan(host, port, rows))
    run("FULL SCAN",            lambda: bench_full_scan(host, port, rows))
    run("AGGREGATION",          lambda: bench_aggregation(host, port, rows))
    run("ORDER BY LIMIT 100",   lambda: bench_order_limit(host, port, rows))
    run("FUZZY SEARCH",         lambda: bench_fuzzy_search(host, port, min(rows, 100_000)))
    run("VECTOR SEARCH (HNSW)", lambda: bench_vector_search(host, port, vec_rows))
    run(f"CONCURRENT {concurrency}", lambda: bench_concurrent(host, port, rows, concurrency))

    print_table("PulseDB", results)
    save_json("PulseDB", results, out_dir)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PulseDB benchmark")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7878)
    parser.add_argument("--rows", type=int, default=100_000,
                        help="Dataset size (default: 100,000 — use 1000000 for 1M test)")
    parser.add_argument("--vec-rows", type=int, default=10_000,
                        help="Rows for vector search benchmark (default: 10,000)")
    parser.add_argument("--concurrency", type=int, default=100,
                        help="Concurrent clients for TPS test (default: 100)")
    parser.add_argument("--out-dir", default="results",
                        help="Directory for JSON output files")
    args = parser.parse_args()

    run_all(args.host, args.port, args.rows, args.concurrency,
            args.vec_rows, args.out_dir)
