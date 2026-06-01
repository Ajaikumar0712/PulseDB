//! Async TCP server for PulseDB.
//!
//! Protocol (line-based text):
//!   client sends: one PulseQL statement per line (ends with \n)
//!   server sends: JSON response + "\n" for each statement
//!
//! Format of server response:
//!   { "status": "ok", "result": <QueryResult as JSON> }
//!   { "status": "error", "message": "<error message>" }
//!
//! One Tokio task per connection. Shared database behind Arc<RwLock>.
//! Each connection gets its own TransactionManager.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use serde_json::json;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tracing::{info, warn};

use crate::api::ApiStore;
use crate::auth::AuthManager;
use crate::cluster::ClusterRegistry;
use crate::cluster::replication::ReplicationManager;
use crate::engine::executor::{Executor, QueryResult};
use crate::storage::disk_store::StorageMode;
use crate::engine::watch::{WatchEvent, WatchRegistry};
use crate::error::FlowError;
use crate::metrics::Metrics;
use crate::sql::ast::Stmt;
use crate::sql::parser::Parser;
use crate::storage::table::Database;
use crate::transaction::TransactionManager;
use crate::triggers::TriggerStore;
use crate::wal::WalWriter;

// ── Server ────────────────────────────────────────────────────────────────

pub struct Server {
    db: Arc<Database>,
    wal: Arc<WalWriter>,
    metrics: Arc<Metrics>,
    addr: SocketAddr,
    data_dir: Option<Arc<PathBuf>>,
    watch_registry: Arc<WatchRegistry>,
    cluster_registry: Option<Arc<ClusterRegistry>>,
    /// Raft-based replication manager (None in single-node mode).
    replication_manager: Option<Arc<ReplicationManager>>,
    /// Storage mode and row-cache limit passed to each Executor.
    storage_mode: StorageMode,
    disk_cache_rows: usize,
    /// Shared trigger store — persists across all connections.
    trigger_store: Arc<TriggerStore>,
    /// Shared REST API server store — persists across all connections.
    api_store: Arc<ApiStore>,
    /// Authentication manager — shared across all connections.
    auth_manager: Arc<AuthManager>,
    /// Optional TLS acceptor. When set, every incoming TCP connection is
    /// wrapped in a TLS handshake before the PulseQL protocol begins.
    tls_acceptor: Option<Arc<tokio_rustls::TlsAcceptor>>,
}

impl Server {
    pub fn new(
        db: Arc<Database>,
        wal: Arc<WalWriter>,
        metrics: Arc<Metrics>,
        addr: SocketAddr,
    ) -> Self {
        Self {
            db,
            wal,
            metrics,
            addr,
            data_dir: None,
            watch_registry: Arc::new(WatchRegistry::new()),
            cluster_registry: None,
            replication_manager: None,
            storage_mode: StorageMode::Memory,
            disk_cache_rows: 500_000,
            trigger_store: Arc::new(TriggerStore::new()),
            api_store: Arc::new(ApiStore::new()),
            auth_manager: Arc::new(AuthManager::open()),
            tls_acceptor: None,
        }
    }

    pub fn with_auth_manager(mut self, am: Arc<AuthManager>) -> Self {
        self.auth_manager = am;
        self
    }

    pub fn with_tls(mut self, acceptor: tokio_rustls::TlsAcceptor) -> Self {
        self.tls_acceptor = Some(Arc::new(acceptor));
        self
    }

    pub fn with_data_dir(mut self, data_dir: PathBuf) -> Self {
        self.data_dir = Some(Arc::new(data_dir));
        self
    }

    pub fn with_watch_registry(mut self, wr: Arc<WatchRegistry>) -> Self {
        self.watch_registry = wr;
        self
    }

    pub fn with_cluster_registry(mut self, cr: Arc<ClusterRegistry>) -> Self {
        self.cluster_registry = Some(cr);
        self
    }

    pub fn with_replication_manager(mut self, rm: Arc<ReplicationManager>) -> Self {
        self.replication_manager = Some(rm);
        self
    }

    pub fn with_storage_mode(mut self, mode: StorageMode, cache_rows: usize) -> Self {
        self.storage_mode = mode;
        self.disk_cache_rows = cache_rows;
        self
    }

    /// Start accepting connections. Runs until the process is killed.
    pub async fn serve(self) -> Result<(), FlowError> {
        let listener = TcpListener::bind(self.addr)
            .await
            .map_err(|e| FlowError::Io(format!("cannot bind to {}: {e}", self.addr)))?;

        info!("PulseDB server listening on {}", self.addr);

        let db            = self.db.clone();
        let wal           = self.wal.clone();
        let metrics       = self.metrics.clone();
        let data_dir      = self.data_dir.clone();
        let watch_reg     = self.watch_registry.clone();
        let cluster_reg   = self.cluster_registry.clone();
        let repl_mgr      = self.replication_manager.clone();
        let storage_mode  = self.storage_mode;
        let disk_cache    = self.disk_cache_rows;
        let trigger_store = self.trigger_store.clone();
        let api_store     = self.api_store.clone();
        let auth_manager  = self.auth_manager.clone();
        let tls_acceptor  = self.tls_acceptor.clone();

        if tls_acceptor.is_some() {
            info!("TLS enabled — connections will be encrypted");
        }

        loop {
            let (tcp, peer) = listener
                .accept()
                .await
                .map_err(|e| FlowError::Io(format!("accept error: {e}")))?;
            info!("new connection from {peer}");

            let db_c   = db.clone();
            let wal_c  = wal.clone();
            let met_c  = metrics.clone();
            let dir_c  = data_dir.clone();
            let wr_c   = watch_reg.clone();
            let cr_c   = cluster_reg.clone();
            let rm_c   = repl_mgr.clone();
            let ts_c   = trigger_store.clone();
            let as_c   = api_store.clone();
            let am_c   = auth_manager.clone();
            let tls_c  = tls_acceptor.clone();

            tokio::spawn(async move {
                let result = if let Some(acceptor) = tls_c {
                    match acceptor.accept(tcp).await {
                        Ok(tls_stream) => {
                            handle_connection(tls_stream, db_c, wal_c, met_c, dir_c, wr_c, cr_c, rm_c, storage_mode, disk_cache, ts_c, as_c, am_c, peer).await
                        }
                        Err(e) => {
                            warn!("TLS handshake failed from {peer}: {e}");
                            return;
                        }
                    }
                } else {
                    handle_connection(tcp, db_c, wal_c, met_c, dir_c, wr_c, cr_c, rm_c, storage_mode, disk_cache, ts_c, as_c, am_c, peer).await
                };
                if let Err(e) = result {
                    warn!("connection {peer} error: {e}");
                }
                info!("connection {peer} closed");
            });
        }
    }
}

// ── Per-connection handler ────────────────────────────────────────────────

fn is_write_stmt(stmt: &Stmt) -> bool {
    matches!(
        stmt,
        Stmt::Put { .. } | Stmt::Set { .. } | Stmt::Del { .. }
    )
}

async fn handle_connection<S>(
    stream: S,
    db: Arc<Database>,
    wal: Arc<WalWriter>,
    metrics: Arc<Metrics>,
    data_dir: Option<Arc<PathBuf>>,
    watch_registry: Arc<WatchRegistry>,
    cluster_registry: Option<Arc<ClusterRegistry>>,
    replication_manager: Option<Arc<ReplicationManager>>,
    storage_mode: StorageMode,
    disk_cache_rows: usize,
    trigger_store: Arc<TriggerStore>,
    api_store: Arc<ApiStore>,
    auth_manager: Arc<AuthManager>,
    _peer: SocketAddr,
) -> Result<(), FlowError>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let (reader, writer) = tokio::io::split(stream);
    let mut lines = BufReader::new(reader).lines();

    // All writes go through a channel so both the main loop and watcher
    // tasks can send responses without fighting over &mut OwnedWriteHalf.
    let (write_tx, mut write_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    tokio::spawn(async move {
        let mut w = writer;
        while let Some(msg) = write_rx.recv().await {
            if w.write_all(msg.as_bytes()).await.is_err() {
                break;
            }
        }
    });

    let executor = match data_dir {
        Some(ref dir) => {
            let mut exec = Executor::with_data_dir(db, metrics.clone(), dir.as_ref().clone());
            exec.config.storage_mode = storage_mode;
            exec.config.disk_cache_rows = disk_cache_rows;
            exec.watch_registry = Some(Arc::clone(&watch_registry));
            exec.cluster_registry = cluster_registry;
            exec.trigger_store = trigger_store;
            exec.api_store = api_store;
            exec.auth_manager = Some(Arc::clone(&auth_manager));
            Arc::new(exec)
        }
        None => {
            let mut exec = Executor::new(db, metrics.clone());
            exec.config.storage_mode = storage_mode;
            exec.config.disk_cache_rows = disk_cache_rows;
            exec.watch_registry = Some(Arc::clone(&watch_registry));
            exec.cluster_registry = cluster_registry;
            exec.trigger_store = trigger_store;
            exec.api_store = api_store;
            exec.auth_manager = Some(Arc::clone(&auth_manager));
            Arc::new(exec)
        }
    };
    let mut tx_mgr = TransactionManager::new(executor, wal);
    let repl = replication_manager;

    // Send banner
    write_tx.send(
        "{\"status\":\"welcome\",\"message\":\"PulseDB \u{2014} ready. Type PulseQL queries.\"}\n".to_string()
    ).ok();

    while let Some(line) = lines
        .next_line()
        .await
        .map_err(|e| FlowError::Io(e.to_string()))?
    {
        let line = line.trim().to_string();
        if line.is_empty() || line.starts_with("--") {
            continue;
        }

        if line.eq_ignore_ascii_case("METRICS") {
            let summary = metrics.summary();
            let resp = json!({ "status": "ok", "metrics": summary }).to_string() + "\n";
            write_tx.send(resp).ok();
            continue;
        }

        let t0 = std::time::Instant::now();
        let query_id = metrics.start_query(&line);

        let stmts = match Parser::parse_str(&line) {
            Ok(s) => s,
            Err(e) => {
                metrics.record_error(&e);
                metrics.finish_query(query_id, t0.elapsed());
                let resp = json!({"status":"error","message":e.to_string()}).to_string() + "\n";
                write_tx.send(resp).ok();
                continue;
            }
        };

        let mut last_result: Option<QueryResult> = None;
        let mut exec_error: Option<FlowError> = None;

        'stmts: for stmt in stmts {
            match stmt {
                Stmt::Watch { table, filter } => {
                    let (row_tx, mut row_rx) =
                        tokio::sync::mpsc::unbounded_channel::<WatchEvent>();
                    let id = watch_registry.subscribe(table, filter, row_tx);
                    let wtx = write_tx.clone();
                    tokio::spawn(async move {
                        while let Some(evt) = row_rx.recv().await {
                            let json_evt = json!({
                                "status": "watch",
                                "id": evt.watch_id,
                                "op": evt.op,
                                "row": evt.row.fields,
                            }).to_string() + "\n";
                            if wtx.send(json_evt).is_err() {
                                break;
                            }
                        }
                    });
                    metrics.finish_query(query_id, t0.elapsed());
                    let ack = json!({
                        "status": "ok",
                        "watch_id": id,
                        "message": format!("watching, subscription id={id}"),
                    }).to_string() + "\n";
                    write_tx.send(ack).ok();
                    continue; // next input line (the outer while loop)
                }
                Stmt::Unwatch { id } => {
                    watch_registry.unsubscribe(id);
                    metrics.finish_query(query_id, t0.elapsed());
                    let ack = json!({
                        "status": "ok",
                        "message": format!("unwatch {id}"),
                    }).to_string() + "\n";
                    write_tx.send(ack).ok();
                    continue; // next input line
                }
                other => {
                    // If replication is active, route writes through Raft before
                    // applying locally so all nodes stay in sync.
                    if is_write_stmt(&other) {
                        if let Some(ref rm) = repl {
                            match rm.route_write(&line).await {
                                Ok(_) => {}
                                Err(e) => {
                                    exec_error = Some(FlowError::internal(e));
                                    break 'stmts;
                                }
                            }
                            // Followers skip local apply — the apply loop handles it
                            // when the committed AppendEntries arrives.
                            if !rm.is_leader() {
                                last_result = Some(QueryResult::ok("replicated", t0.elapsed()));
                                continue 'stmts;
                            }
                        }
                    }
                    match tx_mgr.execute(other) {
                        Ok(r)  => last_result = Some(r),
                        Err(e) => { exec_error = Some(e); break 'stmts; }
                    }
                }
            }
        }

        metrics.finish_query(query_id, t0.elapsed());

        let resp = match exec_error {
            Some(e) => {
                metrics.record_error(&e);
                json!({"status":"error","message":e.to_string()}).to_string()
            }
            None => {
                let r = last_result
                    .unwrap_or_else(|| QueryResult::ok("(empty)", t0.elapsed()));
                json!({"status":"ok","result":r}).to_string()
            }
        };
        write_tx.send(resp + "\n").ok();
    }
    Ok(())
}

