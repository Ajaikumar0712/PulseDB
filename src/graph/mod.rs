//! Graph query engine.
//!
//! PulseDB stores graph edges as regular rows in an edge table with columns:
//!   from_id, to_id, [relation]
//!
//! Syntax:
//!   GRAPH MATCH (a:src_table) -[rel:edge_table]-> (b:dst_table)
//!       WHERE a.col = "value"
//!       [LIMIT n]
//!
//! The engine:
//!   1. Scans `src_table` and applies the WHERE filter to `a.*` columns.
//!   2. For each matching source row, looks up edges in `edge_table` where
//!      `edge_table.from_id` equals the source row's primary key value.
//!   3. For each edge, fetches the corresponding destination row from `dst_table`
//!      by matching `edge_table.to_id` to the destination's primary key.
//!   4. Returns merged result rows: a.*, rel.*, b.* (with table-prefix on collisions).

use std::collections::HashMap;
use std::sync::Arc;

use crate::engine::evaluator::Evaluator;
use crate::error::FlowError;
use crate::sql::ast::Expr;
use crate::storage::table::Database;
use crate::types::Value;

// ── Graph match result ────────────────────────────────────────────────────

/// A single graph match result (one source → edge → destination path).
#[derive(Debug, Clone)]
pub struct GraphRow {
    /// Merged fields: source, edge, destination.
    pub fields: HashMap<String, Value>,
}

// ── Graph engine ──────────────────────────────────────────────────────────

pub struct GraphEngine;

impl GraphEngine {
    /// Execute a GRAPH MATCH query and return merged rows.
    pub fn match_query(
        db: &Arc<Database>,
        src_alias: &str,
        src_table: &str,
        edge_alias: &str,
        edge_table: &str,
        dst_alias: &str,
        dst_table: &str,
        filter: Option<&Expr>,
        limit: usize,
    ) -> Result<Vec<HashMap<String, Value>>, FlowError> {
        // ── Step 1: Scan source table ─────────────────────────────────────
        let src_tbl = db.get_table(src_table)?;
        let src_guard = src_tbl.read().map_err(|_| FlowError::Io("lock poisoned".into()))?;

        // Collect all (non-deleted) source rows — filter applied after merge.
        let src_rows: Vec<HashMap<String, Value>> = src_guard
            .rows
            .values()
            .filter(|r| !r.deleted)
            .map(|row| row.fields.clone())
            .collect();

        // Get source table primary key name
        let src_pk = src_guard
            .schema
            .primary_key()
            .unwrap_or("id")
            .to_string();
        drop(src_guard);

        // ── Step 2 & 3: For each source row, traverse edges ──────────────
        let edge_tbl_result = db.get_table(edge_table);
        let dst_tbl_result  = db.get_table(dst_table);

        // If edge or dst table doesn't exist, return source-only results
        let (edge_tbl, dst_tbl) = match (edge_tbl_result, dst_tbl_result) {
            (Ok(e), Ok(d)) => (e, d),
            _ => {
                // Return just source rows with alias prefix
                let results: Vec<HashMap<String, Value>> = src_rows
                    .into_iter()
                    .take(limit)
                    .map(|fields| {
                        fields
                            .into_iter()
                            .map(|(k, v)| (format!("{}.{}", src_alias, k), v))
                            .collect()
                    })
                    .collect();
                return Ok(results);
            }
        };

        let edge_guard = edge_tbl.read().map_err(|_| FlowError::Io("lock poisoned".into()))?;
        let dst_guard  = dst_tbl.read().map_err(|_| FlowError::Io("lock poisoned".into()))?;

        let dst_pk = dst_guard
            .schema
            .primary_key()
            .unwrap_or("id")
            .to_string();

        // Build a fast lookup: from_id value → list of edge rows
        let mut edge_index: HashMap<String, Vec<HashMap<String, Value>>> = HashMap::new();
        for edge_row in edge_guard.rows.values().filter(|r| !r.deleted) {
            if let Some(from_val) = edge_row.fields.get("from_id") {
                let key = format!("{:?}", from_val);
                edge_index.entry(key).or_default().push(edge_row.fields.clone());
            }
        }

        // Build dst lookup: primary key value → row fields
        let mut dst_index: HashMap<String, HashMap<String, Value>> = HashMap::new();
        for dst_row in dst_guard.rows.values().filter(|r| !r.deleted) {
            if let Some(pk_val) = dst_row.fields.get(&dst_pk) {
                let key = format!("{:?}", pk_val);
                dst_index.insert(key, dst_row.fields.clone());
            }
        }

        let mut results: Vec<HashMap<String, Value>> = Vec::new();

        'outer: for src_fields in src_rows {
            let src_pk_val = src_fields.get(&src_pk).cloned().unwrap_or(Value::Null);
            let src_key = format!("{:?}", src_pk_val);

            if let Some(edges) = edge_index.get(&src_key) {
                for edge_fields in edges {
                    let to_id_val = edge_fields.get("to_id").cloned().unwrap_or(Value::Null);
                    let dst_key = format!("{:?}", to_id_val);

                    if let Some(dst_fields) = dst_index.get(&dst_key) {
                        // Merge: src (aliased), edge (aliased), dst (aliased)
                        let mut merged: HashMap<String, Value> = HashMap::new();

                        for (k, v) in &src_fields {
                            merged.insert(format!("{}.{}", src_alias, k), v.clone());
                        }
                        for (k, v) in edge_fields {
                            merged.insert(format!("{}.{}", edge_alias, k), v.clone());
                        }
                        for (k, v) in dst_fields {
                            merged.insert(format!("{}.{}", dst_alias, k), v.clone());
                        }

                        // Apply WHERE filter on the fully-merged row
                        let passes = if let Some(f) = filter {
                            let merged_row = crate::types::Row::new(merged.clone());
                            Evaluator::eval(f, &merged_row)
                                .map(|v| v.is_truthy())
                                .unwrap_or(false)
                        } else {
                            true
                        };
                        if passes {
                            results.push(merged);
                            if results.len() >= limit {
                                break 'outer;
                            }
                        }
                    }
                }
            }
        }

        Ok(results)
    }

    /// Multi-hop BFS traversal.
    ///
    /// Traverses up to `max_hops` edge-table hops from the source nodes.
    /// All three tables are the same node table; `edge_table` is traversed
    /// repeatedly.
    ///
    /// Returns rows of the form `{ depth, node.<col>, ... }` for each
    /// reachable node, in BFS order.
    ///
    /// ```sql
    /// -- Equivalent of: traverse 'follows' from Alice up to 3 hops
    /// GRAPH MATCH (a:people) -[rel:follows*1..3]-> (b:people)
    ///     WHERE a.name = "Alice" LIMIT 100
    /// ```
    pub fn match_multihop(
        db: &Arc<Database>,
        node_table: &str,
        edge_table: &str,
        node_alias: &str,
        filter: Option<&Expr>,
        min_hops: usize,
        max_hops: usize,
        limit: usize,
    ) -> Result<Vec<HashMap<String, Value>>, FlowError> {
        use std::collections::{HashSet, VecDeque};

        let node_tbl = db.get_table(node_table)?;
        let node_guard = node_tbl.read().map_err(|_| FlowError::Io("lock".into()))?;
        let node_pk = node_guard.schema.primary_key().unwrap_or("id").to_string();

        // Index all node rows by primary key
        let mut node_index: HashMap<String, HashMap<String, Value>> = HashMap::new();
        for row in node_guard.rows.values().filter(|r| !r.deleted) {
            if let Some(pk) = row.fields.get(&node_pk) {
                node_index.insert(format!("{:?}", pk), row.fields.clone());
            }
        }
        drop(node_guard);

        let edge_tbl = db.get_table(edge_table)?;
        let edge_guard = edge_tbl.read().map_err(|_| FlowError::Io("lock".into()))?;

        // Build adjacency list: from_key → [to_key, ...]
        let mut adj: HashMap<String, Vec<String>> = HashMap::new();
        for edge_row in edge_guard.rows.values().filter(|r| !r.deleted) {
            if let (Some(from), Some(to)) = (
                edge_row.fields.get("from_id"),
                edge_row.fields.get("to_id"),
            ) {
                adj.entry(format!("{:?}", from))
                    .or_default()
                    .push(format!("{:?}", to));
            }
        }
        drop(edge_guard);

        // Collect start nodes (matching the WHERE filter at hop 0)
        let start_keys: Vec<String> = node_index
            .iter()
            .filter(|(_, fields)| {
                if let Some(f) = filter {
                    let row = crate::types::Row::new(
                        fields.iter()
                            .map(|(k, v)| (format!("{node_alias}.{k}"), v.clone()))
                            .collect(),
                    );
                    Evaluator::eval(f, &row).map(|v| v.is_truthy()).unwrap_or(false)
                } else {
                    true
                }
            })
            .map(|(k, _)| k.clone())
            .collect();

        // BFS
        let mut results: Vec<HashMap<String, Value>> = Vec::new();
        let mut visited: HashSet<String> = HashSet::new();
        // Queue: (node_key, depth)
        let mut queue: VecDeque<(String, usize)> = start_keys
            .into_iter()
            .map(|k| (k, 0))
            .collect();

        while let Some((key, depth)) = queue.pop_front() {
            if depth > max_hops { continue; }
            if visited.contains(&key) { continue; }
            visited.insert(key.clone());

            if depth >= min_hops {
                if let Some(fields) = node_index.get(&key) {
                    let mut row: HashMap<String, Value> = fields
                        .iter()
                        .map(|(k, v)| (format!("{node_alias}.{k}"), v.clone()))
                        .collect();
                    row.insert("depth".into(), Value::Int(depth as i64));
                    results.push(row);
                    if results.len() >= limit {
                        break;
                    }
                }
            }

            if depth < max_hops {
                if let Some(neighbors) = adj.get(&key) {
                    for neighbor in neighbors {
                        if !visited.contains(neighbor) {
                            queue.push_back((neighbor.clone(), depth + 1));
                        }
                    }
                }
            }
        }

        Ok(results)
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::table::Database;
    use crate::types::{ColumnSchema, DataType, TableSchema, Value};

    fn make_db() -> Arc<Database> {
        let db = Arc::new(Database::new());

        // users table
        let user_schema = TableSchema::new("users", vec![
            ColumnSchema { name: "id".into(), data_type: DataType::Int, nullable: false, primary_key: true },
            ColumnSchema { name: "name".into(), data_type: DataType::Text, nullable: true, primary_key: false },
        ]);
        db.create_table(user_schema).unwrap();
        let mut f1 = HashMap::new();
        f1.insert("id".into(), Value::Int(1));
        f1.insert("name".into(), Value::Text("Alice".into()));
        db.get_table("users").unwrap().write().unwrap().insert(f1).unwrap();
        let mut f2 = HashMap::new();
        f2.insert("id".into(), Value::Int(2));
        f2.insert("name".into(), Value::Text("Bob".into()));
        db.get_table("users").unwrap().write().unwrap().insert(f2).unwrap();

        // follows edge table
        let edge_schema = TableSchema::new("follows", vec![
            ColumnSchema { name: "from_id".into(), data_type: DataType::Int, nullable: false, primary_key: false },
            ColumnSchema { name: "to_id".into(),   data_type: DataType::Int, nullable: false, primary_key: false },
        ]);
        db.create_table(edge_schema).unwrap();
        let mut e1 = HashMap::new();
        e1.insert("from_id".into(), Value::Int(1));
        e1.insert("to_id".into(),   Value::Int(2));
        db.get_table("follows").unwrap().write().unwrap().insert(e1).unwrap();

        db
    }

    #[test]
    fn test_graph_match_no_filter() {
        let db = make_db();
        let rows = GraphEngine::match_query(
            &db, "a", "users", "e", "follows", "b", "users",
            None, 100,
        ).unwrap();
        assert_eq!(rows.len(), 1, "Alice follows Bob — expect 1 path");
        let row = &rows[0];
        assert_eq!(row.get("a.name"), Some(&Value::Text("Alice".into())));
        assert_eq!(row.get("b.name"), Some(&Value::Text("Bob".into())));
    }
}
