#!/usr/bin/env python3
"""
Qdrant comparison benchmark — focused on vector similarity search.

Qdrant is a dedicated vector database. This benchmark compares its
HNSW-based ANN search against PulseDB's built-in HNSW implementation.

Requires:
  pip install qdrant-client
  Qdrant running on localhost:6333 (Docker: docker run -p 6333:6333 qdrant/qdrant)

Environment variables:
  QDRANT_HOST (default: localhost)
  QDRANT_PORT (default: 6333)

Usage:
  python qdrant_bench.py
  python qdrant_bench.py --rows 100000 --dims 128
  python qdrant_bench.py --rows 10000 --concurrency 50
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
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance, VectorParams, PointStruct,
        Filter, FieldCondition, Range,
    )
except ImportError:
    print("Install qdrant-client:  pip install qdrant-client")
    sys.exit(1)


# ── Connection helpers ────────────────────────────────────────────────────────

COLLECTION = "bench"

def connect() -> QdrantClient:
    return QdrantClient(
        host=os.getenv("QDRANT_HOST", "localhost"),
        port=int(os.getenv("QDRANT_PORT", "6333")),
    )


def make_vector(i: int, dims: int) -> list[float]:
    return [round(((i + j) * 0.001) % 1.0, 6) for j in range(dims)]


def setup_collection(client: QdrantClient, dims: int) -> None:
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        COLLECTION,
        vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
    )


def insert_rows(client: QdrantClient, rows: int, dims: int, batch: int = 500) -> None:
    for start in range(0, rows, batch):
        end = min(start + batch, rows)
        points = [
            PointStruct(
                id=i,
                vector=make_vector(i, dims),
                payload={"name": f"item_{i}", "score": round(i * 0.001, 6), "active": i % 2 == 0},
            )
            for i in range(start, end)
        ]
        client.upsert(COLLECTION, points)


# ── Benchmarks ────────────────────────────────────────────────────────────────

def bench_insert(rows: int, dims: int, batch: int = 500) -> BenchResult:
    client = connect()
    setup_collection(client, dims)

    t0 = time.perf_counter()
    for start in range(0, rows, batch):
        end = min(start + batch, rows)
        points = [
            PointStruct(
                id=i,
                vector=make_vector(i, dims),
                payload={"name": f"item_{i}", "score": round(i * 0.001, 6), "active": i % 2 == 0},
            )
            for i in range(start, end)
        ]
        client.upsert(COLLECTION, points)
    elapsed = time.perf_counter() - t0

    tps = rows / elapsed
    lat = LatencyStats(elapsed * 1000 / (rows / batch), 0, 0,
                       elapsed * 1000 / (rows / batch), 0, elapsed * 1000)
    return BenchResult("Qdrant", f"upsert batch={batch} ({dims}-dim)", rows, tps, lat)


def bench_vector_search(rows: int, dims: int, k: int = 10, iterations: int = 100) -> BenchResult:
    client = connect()
    setup_collection(client, dims)
    insert_rows(client, rows, dims)

    query = make_vector(rows // 3, dims)
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        client.search(COLLECTION, query_vector=query, limit=k)
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("Qdrant", f"search k={k} ({dims}-dim, HNSW cosine)", rows,
                       k / (lat.mean_ms / 1000), lat,
                       notes="HNSW approximate NN")


def bench_filtered_vector_search(rows: int, dims: int, k: int = 10, iterations: int = 100) -> BenchResult:
    """Vector search with payload filter (score > 0.5)."""
    client = connect()
    setup_collection(client, dims)
    insert_rows(client, rows, dims)

    query = make_vector(rows // 3, dims)
    score_filter = Filter(
        must=[FieldCondition(key="score", range=Range(gt=0.5))]
    )
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        client.search(COLLECTION, query_vector=query, limit=k, query_filter=score_filter)
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("Qdrant", f"filtered search k={k}, score>0.5 ({dims}-dim)", rows,
                       k / (lat.mean_ms / 1000), lat,
                       notes="HNSW + payload filter")


def bench_point_lookup(rows: int, dims: int, iterations: int = 200) -> BenchResult:
    """Retrieve a point by ID (no vector search)."""
    client = connect()
    setup_collection(client, dims)
    insert_rows(client, rows, dims)
    target = rows // 2

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        client.retrieve(COLLECTION, ids=[target])
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("Qdrant", "retrieve by ID (point)", rows,
                       1 / (lat.mean_ms / 1000), lat)


def bench_scroll(rows: int, dims: int, batch: int = 100, iterations: int = 30) -> BenchResult:
    """Paginate through all points (equivalent to full table scan)."""
    client = connect()
    setup_collection(client, dims)
    insert_rows(client, rows, dims)

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        offset = None
        total = 0
        while True:
            results, offset = client.scroll(
                COLLECTION, limit=batch,
                offset=offset, with_payload=True,
            )
            total += len(results)
            if offset is None:
                break
        times.append(time.perf_counter() - t0)

    lat = latency_stats(times)
    return BenchResult("Qdrant", f"scroll all {rows:,} points (batch={batch})", rows,
                       rows / (lat.mean_ms / 1000), lat,
                       notes="full collection scan")


def bench_concurrent_search(rows: int, dims: int, concurrency: int) -> BenchResult:
    client = connect()
    setup_collection(client, dims)
    insert_rows(client, rows, dims)

    query = make_vector(rows // 3, dims)
    calls_per_thread = 20
    all_times: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker():
        c = connect()
        barrier.wait()
        local = []
        for _ in range(calls_per_thread):
            t0 = time.perf_counter()
            c.search(COLLECTION, query_vector=query, limit=10)
            local.append(time.perf_counter() - t0)
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
    return BenchResult("Qdrant", f"CONCURRENT {concurrency} clients (vector search)", rows,
                       tps, lat, notes=f"{total_ops:,} total searches")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all(rows: int, dims: int, concurrency: int, out_dir: str) -> list[BenchResult]:
    print(f"\nQdrant Benchmarks — rows={rows:,}  dims={dims}  concurrency={concurrency}")
    print("=" * 70)
    results = []

    def run(label: str, fn):
        print(f"  {label}...", end=" ", flush=True)
        t0 = time.perf_counter()
        r = fn()
        elapsed = time.perf_counter() - t0
        results.append(r)
        print(f"{r.tps:,.0f} TPS  p50={r.latency.p50_ms:.2f}ms  [{elapsed:.1f}s]")

    run("INSERT (upsert)",            lambda: bench_insert(rows, dims))
    run("VECTOR SEARCH k=10",         lambda: bench_vector_search(rows, dims))
    run("FILTERED VECTOR SEARCH",     lambda: bench_filtered_vector_search(rows, dims))
    run("POINT LOOKUP by ID",         lambda: bench_point_lookup(rows, dims))
    run("SCROLL (full scan)",         lambda: bench_scroll(min(rows, 50_000), dims))
    run(f"CONCURRENT {concurrency}",  lambda: bench_concurrent_search(rows, dims, concurrency))

    print_table("Qdrant", results)
    save_json("Qdrant", results, out_dir)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qdrant benchmark")
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--dims", type=int, default=128,
                        help="Vector dimensions (default: 128)")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--out-dir", default="results")
    args = parser.parse_args()
    run_all(args.rows, args.dims, args.concurrency, args.out_dir)
