//! Transaction manager — wraps the executor with BEGIN/COMMIT/ROLLBACK semantics.
//!
//! PulseDB uses a simple single-writer, auto-commit-or-explicit transaction model:
//!   - Without BEGIN: every statement is its own transaction (auto-commit).
//!   - With BEGIN … COMMIT: all statements run inside that transaction.
//!   - With BEGIN … ROLLBACK: all buffered WAL records are discarded.
//!
//! Concurrency model: one active transaction at a time per connection (connection is
//! represented by a session). The executor itself is protected by RwLock internally.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use tracing::{debug, info, warn};

use crate::engine::executor::{Executor, QueryResult};
use crate::error::FlowError;
use crate::sql::ast::Stmt;
use crate::wal::{WalRecord, WalWriter};

// ── Global transaction ID counter ─────────────────────────────────────────

static TX_COUNTER: AtomicU64 = AtomicU64::new(1);

fn next_tx_id() -> u64 {
    TX_COUNTER.fetch_add(1, Ordering::SeqCst)
}

// ── Transaction state ─────────────────────────────────────────────────────

#[derive(Debug, PartialEq)]
enum TxState {
    /// No open transaction; each operation auto-commits.
    AutoCommit,
    /// Inside an explicit BEGIN … COMMIT/ROLLBACK block.
    Active { tx_id: u64 },
}

// ── TransactionManager ────────────────────────────────────────────────────

/// A row snapshot captured before an explicit transaction begins.
/// Used to restore state on ROLLBACK.
struct TxSnapshot {
    /// Per-table: snapshot of row UUIDs and their deleted flag at BEGIN time.
    tables: std::collections::HashMap<String, Vec<(uuid::Uuid, bool)>>,
}

/// Sits above the `Executor` and adds WAL-backed transaction support.
pub struct TransactionManager {
    executor: Arc<Executor>,
    wal: Arc<WalWriter>,
    state: TxState,
    /// Buffered WAL records for the current explicit transaction.
    buffer: Vec<WalRecord>,
    /// Snapshot of row state captured at BEGIN for ROLLBACK support.
    pre_tx_snapshot: Option<TxSnapshot>,
}

impl TransactionManager {
    pub fn new(executor: Arc<Executor>, wal: Arc<WalWriter>) -> Self {
        Self {
            executor,
            wal,
            state: TxState::AutoCommit,
            buffer: Vec::new(),
            pre_tx_snapshot: None,
        }
    }

    /// Execute a single statement, handling transaction control transparently.
    pub fn execute(&mut self, stmt: Stmt) -> Result<QueryResult, FlowError> {
        match stmt {
            Stmt::Begin => self.begin(),
            Stmt::Commit => self.commit(),
            Stmt::Rollback => self.rollback(),
            other => self.execute_dml(other),
        }
    }

    // ── Transaction control ───────────────────────────────────────────────

    fn begin(&mut self) -> Result<QueryResult, FlowError> {
        if matches!(self.state, TxState::Active { .. }) {
            return Err(FlowError::TxAlreadyActive);
        }
        let tx_id = next_tx_id();
        self.state = TxState::Active { tx_id };
        self.buffer.clear();

        // Snapshot current row state for ROLLBACK support.
        // Captures (uuid, deleted) for every row in every table.
        let snapshot = {
            let tables_guard = self.executor.db.tables.read().unwrap();
            let mut snap_tables = std::collections::HashMap::new();
            for (name, tbl_lock) in tables_guard.iter() {
                let tbl = tbl_lock.read().unwrap();
                let rows: Vec<(uuid::Uuid, bool)> = tbl.rows.values()
                    .map(|r| (r.id, r.deleted))
                    .collect();
                snap_tables.insert(name.clone(), rows);
            }
            TxSnapshot { tables: snap_tables }
        };
        self.pre_tx_snapshot = Some(snapshot);

        let rec = WalRecord::Begin { tx_id };
        self.wal.append(&rec)?;
        info!("transaction {tx_id} started");
        Ok(QueryResult::ok(format!("BEGIN (tx_id={tx_id})"), std::time::Duration::ZERO))
    }

    fn commit(&mut self) -> Result<QueryResult, FlowError> {
        match self.state {
            TxState::AutoCommit => {
                // Nothing to commit; auto-commit is already done.
                Ok(QueryResult::ok("COMMIT (auto-commit mode)", std::time::Duration::ZERO))
            }
            TxState::Active { tx_id } => {
                // Flush all buffered records to WAL
                let buf_len = self.buffer.len() as u64;
                for rec in &self.buffer {
                    self.wal.append(rec)?;
                }
                self.wal.append(&WalRecord::Commit {
                    tx_id,
                    timestamp: chrono::Utc::now(),
                })?;
                self.executor.metrics.wal_records_written.fetch_add(buf_len + 1, Ordering::Relaxed);
                self.buffer.clear();
                self.state = TxState::AutoCommit;
                self.pre_tx_snapshot = None; // discard snapshot — commit is final
                self.executor.metrics.transactions_committed.fetch_add(1, Ordering::Relaxed);
                info!("transaction {tx_id} committed");
                Ok(QueryResult::ok(format!("COMMIT (tx_id={tx_id})"), std::time::Duration::ZERO))
            }
        }
    }

    fn rollback(&mut self) -> Result<QueryResult, FlowError> {
        match self.state {
            TxState::AutoCommit => {
                warn!("ROLLBACK called outside of an active transaction");
                Ok(QueryResult::ok("ROLLBACK (nothing to roll back)", std::time::Duration::ZERO))
            }
            TxState::Active { tx_id } => {
                self.wal.append(&WalRecord::Rollback { tx_id })?;
                self.buffer.clear();
                self.state = TxState::AutoCommit;
                self.executor.metrics.transactions_rolled_back.fetch_add(1, Ordering::Relaxed);

                // Restore in-memory state from the pre-BEGIN snapshot.
                if let Some(snap) = self.pre_tx_snapshot.take() {
                    let tables_guard = self.executor.db.tables.read().unwrap();
                    for (table_name, snap_rows) in &snap.tables {
                        if let Some(tbl_lock) = tables_guard.get(table_name) {
                            let mut tbl = tbl_lock.write().unwrap();
                            // Build set of row IDs that existed at BEGIN
                            let snap_ids: std::collections::HashMap<uuid::Uuid, bool> =
                                snap_rows.iter().map(|(id, del)| (*id, *del)).collect();
                            // Delete rows inserted during the transaction
                            let to_remove: Vec<uuid::Uuid> = tbl.rows.keys()
                                .filter(|id| !snap_ids.contains_key(id))
                                .copied()
                                .collect();
                            for id in to_remove {
                                tbl.rows.remove(&id);
                            }
                            // Restore deleted flag for rows that existed before BEGIN
                            for (id, was_deleted) in &snap_ids {
                                if let Some(row) = tbl.rows.get_mut(id) {
                                    row.deleted = *was_deleted;
                                }
                            }
                        }
                    }
                    info!("ROLLBACK tx_id={tx_id}: in-memory state restored from snapshot");
                } else {
                    warn!("ROLLBACK tx_id={tx_id}: no snapshot — in-memory state not restored");
                }

                Ok(QueryResult::ok(format!("ROLLBACK (tx_id={tx_id})"), std::time::Duration::ZERO))
            }
        }
    }

    // ── DML execution with WAL logging ────────────────────────────────────

    fn execute_dml(&mut self, stmt: Stmt) -> Result<QueryResult, FlowError> {
        let tx_id = match self.state {
            TxState::AutoCommit => {
                // Auto-commit: assign a fresh tx_id, write BEGIN, execute, write COMMIT
                let id = next_tx_id();
                self.wal.append(&WalRecord::Begin { tx_id: id })?;
                id
            }
            TxState::Active { tx_id } => tx_id,
        };

        // Build and buffer the WAL record(s) for this statement
        let wal_records = wal_records_for_stmt(&stmt, tx_id);

        // Execute the statement
        let result = self.executor.execute(stmt)?;

        // Write WAL records after successful execution
        // Count WAL records written: data records + COMMIT+BEGIN (auto-commit) or buffered
        let is_auto = matches!(self.state, TxState::AutoCommit);
        let wal_written = if is_auto { wal_records.len() as u64 + 2 } else { wal_records.len() as u64 };

        match self.state {
            TxState::AutoCommit => {
                // Write all records + COMMIT immediately
                for rec in &wal_records {
                    self.wal.append(rec)?;
                }
                self.wal.append(&WalRecord::Commit {
                    tx_id,
                    timestamp: chrono::Utc::now(),
                })?;
            }
            TxState::Active { .. } => {
                // Buffer records; commit will flush them later
                self.buffer.extend(wal_records);
            }
        }

        self.executor.metrics.wal_records_written.fetch_add(wal_written, Ordering::Relaxed);
        debug!("tx_id={tx_id} statement executed successfully");
        Ok(result)
    }
}

/// Convert a statement into WAL records.
/// Stores the full serialized Stmt JSON so crash recovery can re-execute it exactly.
fn wal_records_for_stmt(stmt: &Stmt, tx_id: u64) -> Vec<WalRecord> {
    match stmt {
        Stmt::Put { .. }
        | Stmt::Set { .. }
        | Stmt::Del { .. }
        | Stmt::MakeTable { .. }
        | Stmt::DropTable { .. }
        | Stmt::MakeIndex { .. } => {
            match serde_json::to_value(stmt) {
                Ok(stmt_json) => vec![WalRecord::Statement {
                    tx_id,
                    stmt_json,
                    timestamp: chrono::Utc::now(),
                }],
                Err(e) => {
                    tracing::warn!("WAL: failed to serialize stmt — skipping: {e}");
                    vec![]
                }
            }
        }
        _ => vec![], // reads, transactions, admin — no WAL record needed
    }
}
