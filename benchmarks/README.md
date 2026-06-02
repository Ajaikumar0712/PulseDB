# PulseDB Benchmark Suite

End-to-end performance benchmarks comparing PulseDB against **PostgreSQL**, **MongoDB**, **Redis**, and **Qdrant**. All latency numbers are measured against real running database instances.

---

## What is measured

| Benchmark | Description | Scale |
| --- | --- | --- |
| **INSERT** | Bulk row insertion (single + batched transactions) | 1K → 1M rows |
| **POINT LOOKUP** | Fetch one row by primary key (indexed) | p50 / p95 / p99 |
| **RANGE SCAN** | Fetch 10% of the dataset by ID range | rows/sec |
| **FULL SCAN** | Table scan with a float filter (no index) | rows/sec |
| **AGGREGATION** | GROUP BY + COUNT + AVG | rows/sec |
| **ORDER BY LIMIT** | Top-100 sorted by float column | ops/sec |
| **FUZZY SEARCH** | Trigram / regex text similarity search | ops/sec |
| **VECTOR SEARCH** | k-NN cosine similarity (128-dim HNSW) | ops/sec |
| **CONCURRENT TPS** | N simultaneous clients, point lookups | TPS + latency |

---

## Quick start

### 1 — Install Python dependencies

```bash
pip install psycopg2-binary pymongo redis qdrant-client
```

### 2 — Start the databases

```bash
# PulseDB (required)
.\target\release\pulsedb-server.exe --no-auth

# PostgreSQL (optional — skip with --dbs pulsedb redis)
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16

# MongoDB (optional)
docker run -d -p 27017:27017 mongo:7

# Redis (optional)
docker run -d -p 6379:6379 redis:7

# Qdrant (optional — vector search only)
docker run -d -p 6333:6333 qdrant/qdrant
```

### 3 — Run benchmarks

```bash
cd benchmarks

# Quick run — all databases, 100K rows, 100 concurrent clients
python run_all.py

# Full run — 1M rows, 1000 concurrent clients
python run_all.py --rows 1000000 --concurrency 1000

# 10M row test (takes ~30-60 min depending on hardware)
python run_all.py --rows 10000000 --concurrency 1000

# Only PulseDB (no other databases needed)
python run_all.py --dbs pulsedb

# Skip databases that aren't running
python run_all.py --skip-errors

# Vector search comparison (PulseDB vs Qdrant only)
python run_all.py --dbs pulsedb qdrant --vec-rows 100000
```

### 4 — Run Rust internal benchmarks (PulseDB only)

```bash
# All groups
cargo bench

# Specific group
cargo bench -- insert
cargo bench -- vector_search
cargo bench -- mixed_80r_20w

# Save a baseline and compare later
cargo bench -- --save-baseline main
# ...make changes...
cargo bench -- --baseline main

# HTML report
start target/criterion/report/index.html
```

---

## Individual database scripts

You can run each database's benchmark independently:

```bash
cd benchmarks/compare

# PulseDB
python pulsedb_bench.py --rows 1000000 --concurrency 500

# PostgreSQL
PGPASSWORD=postgres python postgres_bench.py --rows 1000000 --concurrency 100

# MongoDB
python mongodb_bench.py --rows 1000000 --concurrency 100

# Redis
python redis_bench.py --rows 1000000 --concurrency 1000

# Qdrant (vector search focused)
python qdrant_bench.py --rows 100000 --dims 128 --concurrency 50
```

---

## Environment variables

| Variable | Default | Used by |
| --- | --- | --- |
| `PULSEDB_HOST` | `127.0.0.1` | pulsedb_bench |
| `PULSEDB_PORT` | `7878` | pulsedb_bench |
| `PGHOST` | `127.0.0.1` | postgres_bench |
| `PGPORT` | `5432` | postgres_bench |
| `PGDATABASE` | `benchmark` | postgres_bench |
| `PGUSER` | `postgres` | postgres_bench |
| `PGPASSWORD` | `postgres` | postgres_bench |
| `MONGO_URI` | `mongodb://localhost:27017` | mongodb_bench |
| `REDIS_HOST` | `127.0.0.1` | redis_bench |
| `REDIS_PORT` | `6379` | redis_bench |
| `QDRANT_HOST` | `localhost` | qdrant_bench |
| `QDRANT_PORT` | `6333` | qdrant_bench |

---

## Output

Each run produces:

- Per-database JSON files in `results/` (e.g. `results/pulsedb_results.json`)
- A cross-database `results/comparison.json` when multiple databases are benchmarked
- A printed ASCII comparison table

```text
══════════════════════════════════════════════════════════
  CROSS-DATABASE COMPARISON — 100,000 rows
  TPS = operations per second   p50/p99 = latency (ms)
══════════════════════════════════════════════════════════
Operation              PulseDB   PostgreSQL   MongoDB   Redis
─────────────────────────────────────────────────────────
INSERT                 125,000       80,000    95,000   450,000 TPS
POINT LOOKUP           280,000      180,000   120,000   800,000 TPS
RANGE SCAN              45,000       90,000    60,000       N/A TPS
FULL SCAN               30,000       50,000    40,000       N/A TPS
AGGREGATION             18,000       25,000    22,000       N/A TPS
VECTOR SEARCH (HNSW)    12,000          N/A       N/A    14,000 TPS
FUZZY SEARCH             8,000       15,000     5,000       N/A TPS
CONCURRENT 100          95,000      140,000    85,000   600,000 TPS
```

> Numbers above are illustrative — run the benchmarks on your hardware for accurate results.

---

## Rust Criterion benchmarks

The `benches/pulseql.rs` file covers PulseDB's internal performance with 12 benchmark groups:

| Group | What it measures |
| --- | --- |
| `insert` | Row insert throughput: 1K, 10K, 100K, 500K, 1M |
| `point_lookup` | Single-row GET by id: dataset 10K / 100K / 1M |
| `range_scan` | Range GET (10% of dataset): 10K / 100K / 1M |
| `full_scan` | Full table scan with float filter: 10K / 100K / 500K |
| `aggregation` | GROUP BY + COUNT + AVG: 10K / 100K / 500K |
| `order_limit` | ORDER BY score DESC LIMIT 100: 10K / 100K / 500K |
| `fuzzy_search` | Trigram ~ operator: 10K / 100K |
| `vector_search` | HNSW 128-dim cosine k=10: 1K / 10K / 50K |
| `transaction` | BEGIN + N writes + COMMIT: 1 / 10 / 50 / 100 ops/tx |
| `parser` | Lex + parse only (8 query types) |
| `mixed_80r_20w` | 80% reads / 20% writes: 10K / 100K |
| `delete` | DEL WHERE active = false (~50% of rows): 1K / 10K / 100K |

---

## Reference hardware

Document your hardware when sharing benchmark numbers:

```text
CPU:    AMD Ryzen 9 5900X / Intel Core i9-13900K
RAM:    32 GB DDR4-3200
Disk:   NVMe SSD (for disk-mode tests)
OS:     Windows 11 / Ubuntu 22.04
Rust:   1.78+ (release profile, LTO enabled)
Python: 3.11+
```

---

## Fairness notes

- All databases are tested **in-memory** where possible (PostgreSQL `shared_buffers`, MongoDB WiredTiger cache, Redis is always in-memory, PulseDB default memory mode)
- Each benchmark opens **fresh connections** — no persistent connection reuse unless the DB client requires it
- Averages are **median of 5+ runs**, first call is discarded as warm-up
- Concurrent tests use a **barrier** so all threads start simultaneously
- PulseDB advantages: no disk I/O, integrated vector/fuzzy search, no ORM overhead
- PulseDB limitations: single-process, no horizontal sharding in v1.0, WAL-only durability
