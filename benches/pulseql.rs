//! PulseDB Criterion benchmarks.
//!
//! Run with:
//!   cargo bench
//!   cargo bench -- <filter>      # e.g. cargo bench -- insert
//!   cargo bench --bench pulseql  # named bench only
//!
//! HTML report: target/criterion/report/index.html

use std::sync::Arc;

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};

use pulsedb::engine::executor::Executor;
use pulsedb::metrics::Metrics;
use pulsedb::sql::parser::Parser;
use pulsedb::storage::table::Database;

// ── Helpers ───────────────────────────────────────────────────────────────

fn make_executor() -> Executor {
    let db = Arc::new(Database::new());
    let metrics = Arc::new(Metrics::new());
    Executor::new(db, metrics)
}

fn exec(ex: &Executor, q: &str) {
    let stmts = Parser::parse_str(q).expect("parse");
    for stmt in stmts {
        ex.execute(stmt).expect("execute");
    }
}

fn setup_table(ex: &Executor) {
    exec(ex, "MAKE TABLE bench (id int, name text, score float, active bool)");
}

fn insert_n(ex: &Executor, n: usize) {
    for i in 0..n {
        exec(ex, &format!(
            r#"PUT bench ({i}, "user_{i}", {:.4}, {})"#,
            (i as f64) * 0.001,
            i % 2 == 0,
        ));
    }
}

// ── INSERT throughput ─────────────────────────────────────────────────────

fn bench_insert(c: &mut Criterion) {
    let mut group = c.benchmark_group("insert");

    for rows in [1_000u64, 10_000, 100_000] {
        group.throughput(Throughput::Elements(rows));
        group.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            b.iter(|| {
                let ex = make_executor();
                setup_table(&ex);
                for i in 0..rows {
                    exec(&ex, &format!(
                        r#"PUT bench ({i}, "u{i}", {:.4}, true)"#,
                        i as f64 * 0.001
                    ));
                }
                black_box(rows)
            });
        });
    }
    group.finish();
}

// ── Point lookup (indexed) ────────────────────────────────────────────────

fn bench_point_lookup(c: &mut Criterion) {
    let mut group = c.benchmark_group("point_lookup");

    for rows in [10_000u64, 100_000] {
        group.throughput(Throughput::Elements(1));
        group.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            exec(&ex, "MAKE INDEX bench ON id");
            insert_n(&ex, rows as usize);

            let target = rows / 2;
            b.iter(|| {
                exec(&ex, black_box(&format!("GET bench WHERE id = {target}")));
            });
        });
    }
    group.finish();
}

// ── Full scan ─────────────────────────────────────────────────────────────

fn bench_full_scan(c: &mut Criterion) {
    let mut group = c.benchmark_group("full_scan");

    for rows in [1_000u64, 10_000, 50_000] {
        group.throughput(Throughput::Elements(rows));
        group.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);

            b.iter(|| {
                exec(&ex, black_box("GET bench WHERE score > 0.5"));
            });
        });
    }
    group.finish();
}

// ── Range scan (indexed) ──────────────────────────────────────────────────

fn bench_range_scan(c: &mut Criterion) {
    let mut group = c.benchmark_group("range_scan_indexed");

    for rows in [10_000u64, 100_000] {
        group.throughput(Throughput::Elements(rows / 10)); // ~10% selectivity
        group.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            exec(&ex, "MAKE INDEX bench ON id");
            insert_n(&ex, rows as usize);

            let lo = rows / 10;
            let hi = rows / 10 * 2;
            b.iter(|| {
                exec(&ex, black_box(&format!("GET bench WHERE id > {lo} AND id < {hi}")));
            });
        });
    }
    group.finish();
}

// ── Fuzzy text search (trigram) ───────────────────────────────────────────

fn bench_fuzzy_search(c: &mut Criterion) {
    let mut group = c.benchmark_group("fuzzy_text_search");
    group.throughput(Throughput::Elements(1));

    for rows in [1_000u64, 5_000] {
        group.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            for i in 0..rows {
                exec(&ex, &format!(r#"PUT bench ({i}, "username_{i}_handle", 0.0, true)"#));
            }

            b.iter(|| {
                exec(&ex, black_box("FIND bench WHERE name ~ \"usrname\""));
            });
        });
    }
    group.finish();
}

// ── Vector similarity search ──────────────────────────────────────────────

fn bench_vector_search(c: &mut Criterion) {
    let mut group = c.benchmark_group("vector_similarity");
    group.throughput(Throughput::Elements(1));

    // Table with a vector column
    for rows in [500u64, 2_000] {
        group.bench_with_input(BenchmarkId::new("hnsw", rows), &rows, |b, &rows| {
            let ex = make_executor();
            exec(&ex, "MAKE TABLE vecs (id int, emb vector(4))");
            exec(&ex, "MAKE INDEX vecs ON emb HNSW");
            for i in 0..rows {
                let v = format!("[{:.3},{:.3},{:.3},{:.3}]",
                    (i as f64).sin(), (i as f64).cos(),
                    (i as f64 * 0.7).sin(), (i as f64 * 0.3).cos());
                exec(&ex, &format!("PUT vecs ({i}, {v})"));
            }

            b.iter(|| {
                exec(&ex, black_box("SIMILAR vecs TO [0.1, 0.9, 0.2, 0.8] LIMIT 10"));
            });
        });
    }
    group.finish();
}

// ── Aggregation (GROUP BY + COUNT) ────────────────────────────────────────

fn bench_aggregation(c: &mut Criterion) {
    let mut group = c.benchmark_group("aggregation");

    for rows in [10_000u64, 50_000] {
        group.throughput(Throughput::Elements(rows));
        group.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);

            b.iter(|| {
                exec(&ex, black_box("GET bench GROUP BY active HAVING COUNT(*) > 0"));
            });
        });
    }
    group.finish();
}

// ── Transaction throughput ────────────────────────────────────────────────

fn bench_transactions(c: &mut Criterion) {
    use pulsedb::transaction::TransactionManager;
    use pulsedb::wal::WalWriter;

    let mut group = c.benchmark_group("transactions");

    for ops_per_tx in [1u64, 10, 100] {
        group.throughput(Throughput::Elements(ops_per_tx));
        group.bench_with_input(BenchmarkId::from_parameter(ops_per_tx), &ops_per_tx, |b, &ops| {
            let ex = make_executor();
            setup_table(&ex);
            let ex = Arc::new(ex);
            let wal = Arc::new(WalWriter::open(tempfile::NamedTempFile::new().unwrap().path()).unwrap());
            let mut tx_mgr = TransactionManager::new(ex, wal);

            b.iter(|| {
                let stmts = Parser::parse_str("BEGIN").unwrap();
                for s in stmts { tx_mgr.execute(s).unwrap(); }

                for i in 0..ops {
                    let stmts = Parser::parse_str(&format!(
                        r#"PUT bench ({i}, "tx_user", 1.0, true)"#
                    )).unwrap();
                    for s in stmts { tx_mgr.execute(s).unwrap(); }
                }

                let stmts = Parser::parse_str("COMMIT").unwrap();
                for s in stmts { tx_mgr.execute(s).unwrap(); }
            });
        });
    }
    group.finish();
}

// ── Register all groups ───────────────────────────────────────────────────

criterion_group!(
    benches,
    bench_insert,
    bench_point_lookup,
    bench_full_scan,
    bench_range_scan,
    bench_fuzzy_search,
    bench_vector_search,
    bench_aggregation,
    bench_transactions,
);
criterion_main!(benches);
