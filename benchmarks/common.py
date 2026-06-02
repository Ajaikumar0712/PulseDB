"""
Shared utilities for PulseDB comparison benchmarks.

Each benchmark script imports this module to get:
  - BenchResult      : structured timing result
  - latency_stats    : compute p50 / p95 / p99 / mean from a list of seconds
  - run_concurrent   : fire N threads each doing a task, collect latencies
  - print_table      : pretty-print results as an aligned ASCII table
  - save_json        : write results to JSON for run_all.py aggregation
"""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class LatencyStats:
    p50_ms:  float
    p95_ms:  float
    p99_ms:  float
    mean_ms: float
    min_ms:  float
    max_ms:  float

    def __str__(self) -> str:
        return (
            f"p50={self.p50_ms:.2f}ms  p95={self.p95_ms:.2f}ms  "
            f"p99={self.p99_ms:.2f}ms  mean={self.mean_ms:.2f}ms"
        )


@dataclass
class BenchResult:
    database:  str
    operation: str
    rows:      int
    tps:       float          # operations per second
    latency:   LatencyStats
    notes:     str = ""

    def row(self) -> list:
        return [
            self.operation,
            f"{self.rows:,}",
            f"{self.tps:,.0f}",
            f"{self.latency.p50_ms:.2f}",
            f"{self.latency.p95_ms:.2f}",
            f"{self.latency.p99_ms:.2f}",
            self.notes,
        ]


# ── Core helpers ──────────────────────────────────────────────────────────────

def latency_stats(times_sec: List[float]) -> LatencyStats:
    """Compute latency percentiles from a list of elapsed-second measurements."""
    if not times_sec:
        z = LatencyStats(0, 0, 0, 0, 0, 0)
        return z
    ms = sorted(t * 1000 for t in times_sec)
    n  = len(ms)

    def percentile(p: float) -> float:
        idx = (p / 100) * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return ms[lo] + frac * (ms[hi] - ms[lo])

    return LatencyStats(
        p50_ms  = percentile(50),
        p95_ms  = percentile(95),
        p99_ms  = percentile(99),
        mean_ms = statistics.mean(ms),
        min_ms  = ms[0],
        max_ms  = ms[-1],
    )


def run_timed(fn: Callable, iterations: int = 5) -> LatencyStats:
    """
    Run fn() `iterations` times, return latency stats.
    First call is a warm-up (discarded).
    """
    fn()  # warm-up
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return latency_stats(times)


def run_concurrent(
    fn: Callable,
    *,
    concurrency: int,
    calls_per_thread: int,
) -> tuple[float, LatencyStats]:
    """
    Run fn() across `concurrency` threads, each calling it `calls_per_thread` times.

    Returns:
        (overall_tps, latency_stats_across_all_calls)
    """
    all_times: List[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker():
        barrier.wait()  # synchronized start
        local = []
        for _ in range(calls_per_thread):
            t0 = time.perf_counter()
            fn()
            local.append(time.perf_counter() - t0)
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
    tps = total_ops / wall_elapsed if wall_elapsed > 0 else 0
    return tps, latency_stats(all_times)


# ── Output helpers ────────────────────────────────────────────────────────────

HEADER = ["Operation", "Rows", "TPS", "p50 (ms)", "p95 (ms)", "p99 (ms)", "Notes"]

def print_table(db_name: str, results: List[BenchResult]) -> None:
    rows = [HEADER] + [r.row() for r in results]
    widths = [max(len(str(rows[i][j])) for i in range(len(rows))) for j in range(len(HEADER))]

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"

    print(f"\n{'=' * (sum(widths) + 3 * len(widths) + 1)}")
    print(f"  {db_name}")
    print(f"{'=' * (sum(widths) + 3 * len(widths) + 1)}")
    print(sep)
    print(fmt.format(*HEADER))
    print(sep.replace("-", "="))
    for r in results:
        print(fmt.format(*r.row()))
    print(sep)
    print()


def save_json(db_name: str, results: List[BenchResult], out_dir: str = ".") -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe = db_name.lower().replace(" ", "_")
    path = os.path.join(out_dir, f"{safe}_results.json")
    data = {
        "database": db_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [asdict(r) for r in results],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Results saved → {path}")
    return path


# ── Memory helper ─────────────────────────────────────────────────────────────

def get_rss_mb() -> float:
    """Return current process RSS in MB (cross-platform best-effort)."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
