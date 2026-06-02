//! PulseDB comprehensive Criterion benchmarks.
//!
//! Covers:
//!   INSERT throughput   (1K → 1M rows)
//!   Point lookup        (indexed, 10K / 100K / 1M dataset)
//!   Range scan          (10% of dataset)
//!   Full table scan     (float filter, no index)
//!   Aggregation         (GROUP BY + COUNT + AVG)
//!   ORDER BY + LIMIT    (top-100 sorted)
//!   Fuzzy text search   (trigram ~)
//!   Vector similarity   (SIMILAR / HNSW, 128-dim)
//!   Transaction TPS     (BEGIN + N writes + COMMIT)
//!   Parser throughput   (lex + parse only, no execution)
//!   Mixed workload      (80% reads / 20% writes)
//!
//! Run:
//!   cargo bench                           # all groups
//!   cargo bench -- insert                 # insert group only
//!   cargo bench -- --save-baseline main   # save for comparison
//!   cargo bench -- --baseline main        # diff against saved baseline
//!
//! HTML report: target/criterion/report/index.html

#![allow(dead_code)]

use std::sync::Arc;

use criterion::{
    black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput,
};

use pulsedb::engine::executor::Executor;
use pulsedb::metrics::Metrics;
use pulsedb::sql::parser::Parser;
use pulsedb::storage::table::Database;

// ── Helpers ───────────────────────────────────────────────────────────────

fn make_executor() -> Executor {
    Executor::new(Arc::new(Database::new()), Arc::new(Metrics::new()))
}

fn exec(ex: &Executor, q: &str) {
    let stmts = Parser::parse_str(q).expect("parse");
    for s in stmts {
        ex.execute(s).expect("execute");
    }
}

fn setup_table(ex: &Executor) {
    exec(
        ex,
        "MAKE TABLE bench (id int PRIMARY KEY, name text, score float, active bool)",
    );
}

fn insert_n(ex: &Executor, n: usize) {
    for i in 0..n {
        exec(
            ex,
            &format!(
                r#"PUT bench {{ id: {i}, name: "user_{i}", score: {:.6}, active: {} }}"#,
                (i as f64) * 0.001,
                if i % 2 == 0 { "true" } else { "false" },
            ),
        );
    }
}

// ── 1. INSERT throughput ──────────────────────────────────────────────────

fn bench_insert(c: &mut Criterion) {
    let mut g = c.benchmark_group("insert");
    g.sample_size(10);

    for &rows in &[1_000u64, 10_000, 100_000, 500_000, 1_000_000] {
        g.throughput(Throughput::Elements(rows));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            b.iter(|| {
                let ex = make_executor();
                setup_table(&ex);
                insert_n(&ex, rows as usize);
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── 2. Point lookup ───────────────────────────────────────────────────────

fn bench_point_lookup(c: &mut Criterion) {
    let mut g = c.benchmark_group("point_lookup");
    g.sample_size(50);

    for &rows in &[10_000u64, 100_000, 1_000_000] {
        g.throughput(Throughput::Elements(1));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            let target = rows / 2;
            b.iter(|| {
                exec(&ex, &format!("GET bench WHERE id = {target}"));
                black_box(target)
            });
        });
    }
    g.finish();
}

// ── 3. Range scan (10% of dataset) ────────────────────────────────────────

fn bench_range_scan(c: &mut Criterion) {
    let mut g = c.benchmark_group("range_scan");
    g.sample_size(20);

    for &rows in &[10_000u64, 100_000, 1_000_000] {
        let result_count = rows / 10;
        g.throughput(Throughput::Elements(result_count));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            let lo = rows / 10;
            let hi = rows / 10 * 2;
            b.iter(|| {
                exec(&ex, &format!("GET bench WHERE id >= {lo} AND id < {hi}"));
                black_box((lo, hi))
            });
        });
    }
    g.finish();
}

// ── 4. Full scan with filter ──────────────────────────────────────────────

fn bench_full_scan(c: &mut Criterion) {
    let mut g = c.benchmark_group("full_scan");
    g.sample_size(10);

    for &rows in &[10_000u64, 100_000, 500_000] {
        g.throughput(Throughput::Elements(rows));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            b.iter(|| {
                exec(&ex, "GET bench WHERE score > 0.5");
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── 5. Aggregation (GROUP BY + COUNT + AVG) ───────────────────────────────

fn bench_aggregation(c: &mut Criterion) {
    let mut g = c.benchmark_group("aggregation");
    g.sample_size(10);

    for &rows in &[10_000u64, 100_000, 500_000] {
        g.throughput(Throughput::Elements(rows));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            b.iter(|| {
                exec(
                    &ex,
                    "GET bench GROUP BY active COUNT(*) AS cnt AVG(score) AS avg_score",
                );
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── 6. ORDER BY + LIMIT ───────────────────────────────────────────────────

fn bench_order_limit(c: &mut Criterion) {
    let mut g = c.benchmark_group("order_limit");
    g.sample_size(20);

    for &rows in &[10_000u64, 100_000, 500_000] {
        g.throughput(Throughput::Elements(100));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            b.iter(|| {
                exec(&ex, "GET bench ORDER BY score DESC LIMIT 100");
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── 7. Fuzzy text search (trigram ~) ──────────────────────────────────────

fn bench_fuzzy_search(c: &mut Criterion) {
    let mut g = c.benchmark_group("fuzzy_search");
    g.sample_size(20);

    for &rows in &[10_000u64, 100_000] {
        g.throughput(Throughput::Elements(rows));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            b.iter(|| {
                exec(&ex, r#"FIND bench WHERE name ~ "user_5" LIMIT 20"#);
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── 8. Vector similarity search (HNSW, 128-dim) ───────────────────────────

fn bench_vector_search(c: &mut Criterion) {
    let mut g = c.benchmark_group("vector_search");
    g.sample_size(20);

    for &rows in &[1_000u64, 10_000, 50_000] {
        g.throughput(Throughput::Elements(10)); // k=10 nearest neighbours
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            exec(&ex, "MAKE TABLE vecs (id int PRIMARY KEY, embedding vector)");

            for i in 0..rows {
                let v: Vec<f64> = (0..128).map(|j| ((i + j) as f64 * 0.001) % 1.0).collect();
                let vec_str = v.iter().map(|x| format!("{x:.6}")).collect::<Vec<_>>().join(", ");
                exec(&ex, &format!("PUT vecs {{ id: {i}, embedding: [{vec_str}] }}"));
            }

            let query: Vec<f64> = (0..128).map(|j| (j as f64 * 0.0007) % 1.0).collect();
            let q_str = query.iter().map(|x| format!("{x:.6}")).collect::<Vec<_>>().join(", ");
            let stmt = format!("SIMILAR vecs ON embedding TO [{q_str}] LIMIT 10");

            b.iter(|| {
                exec(&ex, &stmt);
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── 9. Transaction TPS (BEGIN + N writes + COMMIT) ────────────────────────

fn bench_transaction(c: &mut Criterion) {
    let mut g = c.benchmark_group("transaction");
    g.sample_size(50);

    for &ops_per_tx in &[1u64, 10, 50, 100] {
        g.throughput(Throughput::Elements(ops_per_tx));
        g.bench_with_input(
            BenchmarkId::from_parameter(ops_per_tx),
            &ops_per_tx,
            |b, &ops| {
                let ex = make_executor();
                setup_table(&ex);
                let mut counter = 0u64;

                b.iter(|| {
                    exec(&ex, "BEGIN");
                    for _ in 0..ops {
                        exec(
                            &ex,
                            &format!(
                                r#"PUT bench {{ id: {counter}, name: "tx", score: 0.5, active: true }}"#
                            ),
                        );
                        counter += 1;
                    }
                    exec(&ex, "COMMIT");
                    black_box(counter)
                });
            },
        );
    }
    g.finish();
}

// ── 10. Parser throughput (lex + parse only) ──────────────────────────────

fn bench_parser(c: &mut Criterion) {
    let mut g = c.benchmark_group("parser");
    g.sample_size(200);

    let queries: &[(&str, &str)] = &[
        ("put",     r#"PUT bench { id: 1, name: "Alice", score: 0.42, active: true }"#),
        ("get",     "GET bench WHERE id = 42 AND active = true"),
        ("range",   "GET bench WHERE score > 0.25 AND score < 0.75 ORDER BY score DESC LIMIT 100"),
        ("join",    "GET users INNER JOIN orders ON users.id = orders.user_id WHERE orders.total > 50.0"),
        ("aggr",    "GET bench GROUP BY active COUNT(*) AS cnt SUM(score) AS total HAVING cnt > 1"),
        ("find",    r#"FIND bench WHERE name ~ "alic" LIMIT 10"#),
        ("similar", "SIMILAR bench ON embedding TO [0.1, 0.2, 0.3] LIMIT 5"),
        ("graph",   "GRAPH MATCH (a:users) -[rel:follows]-> (b:users) WHERE a.id = 1 LIMIT 10"),
    ];

    for (name, q) in queries {
        g.throughput(Throughput::Elements(1));
        g.bench_with_input(BenchmarkId::new("parse", name), q, |b, q| {
            b.iter(|| {
                let stmts = Parser::parse_str(black_box(q)).expect("parse");
                black_box(stmts)
            });
        });
    }
    g.finish();
}

// ── 11. Mixed workload (80% reads / 20% writes) ───────────────────────────

fn bench_mixed_workload(c: &mut Criterion) {
    let mut g = c.benchmark_group("mixed_80r_20w");
    g.sample_size(20);

    for &rows in &[10_000u64, 100_000] {
        g.throughput(Throughput::Elements(100));
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            let ex = make_executor();
            setup_table(&ex);
            insert_n(&ex, rows as usize);
            let mut write_counter = rows as usize;

            b.iter(|| {
                for i in 0..100u64 {
                    if i % 5 == 0 {
                        exec(
                            &ex,
                            &format!(
                                r#"PUT bench {{ id: {write_counter}, name: "new", score: 0.5, active: true }}"#
                            ),
                        );
                        write_counter += 1;
                    } else {
                        exec(&ex, &format!("GET bench WHERE id = {}", i % rows));
                    }
                }
                black_box(write_counter)
            });
        });
    }
    g.finish();
}

// ── 12. DELETE throughput ─────────────────────────────────────────────────

fn bench_delete(c: &mut Criterion) {
    let mut g = c.benchmark_group("delete");
    g.sample_size(20);

    for &rows in &[1_000u64, 10_000, 100_000] {
        g.throughput(Throughput::Elements(rows / 2)); // delete half
        g.bench_with_input(BenchmarkId::from_parameter(rows), &rows, |b, &rows| {
            b.iter(|| {
                let ex = make_executor();
                setup_table(&ex);
                insert_n(&ex, rows as usize);
                // Delete all inactive rows (~50%)
                exec(&ex, "DEL bench WHERE active = false");
                black_box(rows)
            });
        });
    }
    g.finish();
}

// ── Registry ──────────────────────────────────────────────────────────────

criterion_group!(
    benches,
    bench_insert,
    bench_point_lookup,
    bench_range_scan,
    bench_full_scan,
    bench_aggregation,
    bench_order_limit,
    bench_fuzzy_search,
    bench_vector_search,
    bench_transaction,
    bench_parser,
    bench_mixed_workload,
    bench_delete,
);
criterion_main!(benches);
