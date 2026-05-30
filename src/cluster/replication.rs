//! Cluster replication — TCP transport for Raft RPCs + state-machine apply.
//!
//! # Architecture
//!
//! Each node listens on a **replication port** (server port + 1, default 7879).
//! Raft RPCs travel as newline-delimited JSON on that port.
//!
//! Write flow:
//!   Leader   — propose(raw_query) → Raft quorum → apply to local DB → ack client.
//!   Follower — forward raw_query to leader → leader acks → client done.
//!              Followers also apply locally when committed AppendEntries arrive.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, oneshot, RwLock};
use tracing::{debug, info, warn};

use crate::cluster::raft::{
    AppendEntries, AppendEntriesReply, LogEntry, NodeId, RaftNode, RequestVote, RequestVoteReply,
};
use crate::storage::table::Database;

// ── Wire protocol ─────────────────────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "rpc", content = "data")]
enum RpcMessage {
    RequestVote(RvWire),
    RequestVoteReply(RvrWire),
    AppendEntries(AeWire),
    AppendEntriesReply(AerWire),
    /// Follower forwards a write to the leader.
    ForwardWrite { from_id: u64, query: String },
    /// Leader acknowledges a forwarded write (after quorum commit).
    WriteAck { ok: bool, error: Option<String> },
}

// Serialisable wrappers (the raft structs don't derive Serialize).

#[derive(Debug, Serialize, Deserialize, Clone)]
struct RvWire {
    term: u64,
    candidate_id: u64,
    last_log_index: u64,
    last_log_term: u64,
}

#[derive(Debug, Serialize, Deserialize)]
struct RvrWire {
    from: u64,
    term: u64,
    vote_granted: bool,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct AeEntry {
    term: u64,
    index: u64,
    // log entry data stored as UTF-8 (PulseQL queries are always UTF-8)
    data: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct AeWire {
    term: u64,
    leader_id: u64,
    prev_log_index: u64,
    prev_log_term: u64,
    entries: Vec<AeEntry>,
    leader_commit: u64,
}

#[derive(Debug, Serialize, Deserialize)]
struct AerWire {
    from: u64,
    term: u64,
    success: bool,
    match_index: u64,
}

impl RvWire {
    fn to_raft(&self) -> RequestVote {
        RequestVote {
            term: self.term,
            candidate_id: NodeId(self.candidate_id),
            last_log_index: self.last_log_index,
            last_log_term: self.last_log_term,
        }
    }
}

impl From<&RequestVote> for RvWire {
    fn from(v: &RequestVote) -> Self {
        Self {
            term: v.term,
            candidate_id: v.candidate_id.0,
            last_log_index: v.last_log_index,
            last_log_term: v.last_log_term,
        }
    }
}

impl AeWire {
    fn to_raft(&self) -> AppendEntries {
        AppendEntries {
            term: self.term,
            leader_id: NodeId(self.leader_id),
            prev_log_index: self.prev_log_index,
            prev_log_term: self.prev_log_term,
            entries: self.entries.iter().map(|e| LogEntry {
                term: e.term,
                index: e.index,
                data: e.data.as_bytes().to_vec(),
            }).collect(),
            leader_commit: self.leader_commit,
        }
    }
}

impl From<&AppendEntries> for AeWire {
    fn from(ae: &AppendEntries) -> Self {
        Self {
            term: ae.term,
            leader_id: ae.leader_id.0,
            prev_log_index: ae.prev_log_index,
            prev_log_term: ae.prev_log_term,
            entries: ae.entries.iter().map(|e| AeEntry {
                term: e.term,
                index: e.index,
                data: String::from_utf8_lossy(&e.data).into_owned(),
            }).collect(),
            leader_commit: ae.leader_commit,
        }
    }
}

// ── Peer map ──────────────────────────────────────────────────────────────

/// Maps NodeId (u64) → replication port address (e.g. "10.0.0.2:7879").
pub type PeerMap = Arc<RwLock<HashMap<u64, String>>>;

// ── Pending write tracker ─────────────────────────────────────────────────

struct PendingWrite {
    log_index: u64,
    tx: oneshot::Sender<Result<(), String>>,
}

// ── Apply command ─────────────────────────────────────────────────────────

pub(crate) struct ApplyCmd {
    pub index: u64,
    pub query: String,
}

// ── Replication manager ───────────────────────────────────────────────────

/// Owns the Raft node and manages cluster replication for one PulseDB node.
pub struct ReplicationManager {
    pub node_id: NodeId,
    pub raft: Arc<RaftNode>,
    pub peers: PeerMap,
    pending: Arc<Mutex<Vec<PendingWrite>>>,
    leader_addr: Arc<Mutex<Option<String>>>,
    /// Sender into the apply loop — set once by `start()`.
    apply_tx: Mutex<Option<mpsc::UnboundedSender<ApplyCmd>>>,
}

impl ReplicationManager {
    pub fn new(node_id: u64, peers: PeerMap) -> Arc<Self> {
        Arc::new(Self {
            node_id: NodeId(node_id),
            raft: Arc::new(RaftNode::new(NodeId(node_id), Vec::new())),
            peers,
            pending: Arc::new(Mutex::new(Vec::new())),
            leader_addr: Arc::new(Mutex::new(None)),
            apply_tx: Mutex::new(None),
        })
    }

    pub fn is_leader(&self) -> bool {
        self.raft.is_leader()
    }

    /// Returns the known leader's replication address, if any.
    pub fn leader_rpc_addr(&self) -> Option<String> {
        self.leader_addr.lock().unwrap().clone()
    }

    /// Add a peer: id → replication address.
    pub async fn add_peer(&self, id: u64, addr: String) {
        self.peers.write().await.insert(id, addr);
        // Update the Raft peer list.
        let mut state = self.raft.state.lock().unwrap();
        if !state.peers.contains(&NodeId(id)) {
            state.peers.push(NodeId(id));
        }
    }

    /// Start background tasks.  Must be called once before `propose_write`.
    /// `rpc_addr` — address this node listens on for Raft RPC traffic.
    /// `db`       — the database to apply committed entries to.
    pub fn start(self: &Arc<Self>, rpc_addr: SocketAddr, db: Arc<Database>) {
        let (apply_tx, apply_rx) = mpsc::unbounded_channel::<ApplyCmd>();
        *self.apply_tx.lock().unwrap() = Some(apply_tx.clone());

        self.spawn_apply_loop(apply_rx, db);
        self.spawn_ticker(apply_tx);
        self.spawn_rpc_listener(rpc_addr);
    }

    // ── Internal task spawners ────────────────────────────────────────────

    fn spawn_apply_loop(self: &Arc<Self>, mut rx: mpsc::UnboundedReceiver<ApplyCmd>, db: Arc<Database>) {
        let pending = Arc::clone(&self.pending);
        tokio::spawn(async move {
            while let Some(cmd) = rx.recv().await {
                debug!(index = cmd.index, query = %cmd.query, "applying committed entry");
                apply_write_to_db(&db, &cmd.query).await;
                // Drain entries matching this index and notify waiters.
                let mut waiters = pending.lock().unwrap();
                let remaining: Vec<PendingWrite> = waiters.drain(..)
                    .filter_map(|w| {
                        if w.log_index == cmd.index {
                            let _ = w.tx.send(Ok(()));
                            None
                        } else {
                            Some(w)
                        }
                    })
                    .collect();
                *waiters = remaining;
            }
        });
    }

    fn spawn_ticker(self: &Arc<Self>, apply_tx: mpsc::UnboundedSender<ApplyCmd>) {
        let mgr = Arc::clone(self);
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(Duration::from_millis(10));
            loop {
                ticker.tick().await;

                // Election timeout check
                if let Some(vote_req) = mgr.raft.tick() {
                    info!(id = mgr.node_id.0, term = vote_req.term, "starting election");
                    let peers_snap: Vec<String> = mgr.peers.read().await.values().cloned().collect();
                    for peer_addr in peers_snap {
                        let msg = RpcMessage::RequestVote(RvWire::from(&vote_req));
                        let raft = Arc::clone(&mgr.raft);
                        let my_id = mgr.node_id;
                        let pa = peer_addr.clone();
                        tokio::spawn(async move {
                            if let Ok(Some(RpcMessage::RequestVoteReply(r))) = send_rpc(&pa, msg).await {
                                let reply = RequestVoteReply { term: r.term, vote_granted: r.vote_granted };
                                let mut votes = 1;
                                raft.on_request_vote_reply(my_id, reply, &mut votes);
                            }
                        });
                    }
                }

                // Leader: send heartbeat / replicate new entries to each peer
                if mgr.raft.is_leader() {
                    let peers_snap: Vec<(u64, String)> = mgr.peers.read().await
                        .iter().map(|(id, addr)| (*id, addr.clone())).collect();

                    for (peer_id, peer_addr) in peers_snap {
                        let ae_wire = {
                            let state = mgr.raft.state.lock().unwrap();
                            let ni = state.next_index.get(&NodeId(peer_id)).copied().unwrap_or(1);
                            let entries = if ni as usize <= state.log.len().saturating_sub(1) {
                                state.log[ni as usize..].to_vec()
                            } else {
                                Vec::new()
                            };
                            let prev_idx = ni.saturating_sub(1);
                            let prev_term = state.log.get(prev_idx as usize)
                                .map(|e| e.term).unwrap_or(0);
                            let ae = AppendEntries {
                                term: state.current_term,
                                leader_id: state.id,
                                prev_log_index: prev_idx,
                                prev_log_term: prev_term,
                                entries,
                                leader_commit: state.commit_index,
                            };
                            AeWire::from(&ae)
                        };

                        let raft = Arc::clone(&mgr.raft);
                        let atx = apply_tx.clone();
                        let pa = peer_addr.clone();
                        let pid = peer_id;
                        let prev_commit = mgr.raft.commit_index();
                        tokio::spawn(async move {
                            if let Ok(Some(RpcMessage::AppendEntriesReply(r))) = send_rpc(&pa, RpcMessage::AppendEntries(ae_wire)).await {
                                let reply = AppendEntriesReply {
                                    term: r.term,
                                    success: r.success,
                                    match_index: r.match_index,
                                };
                                raft.on_append_entries_reply(NodeId(pid), reply);
                                let new_commit = raft.commit_index();
                                if new_commit > prev_commit {
                                    let state = raft.state.lock().unwrap();
                                    for idx in (prev_commit + 1)..=new_commit {
                                        if let Some(entry) = state.log.get(idx as usize) {
                                            let q = String::from_utf8_lossy(&entry.data).into_owned();
                                            let _ = atx.send(ApplyCmd { index: idx, query: q });
                                        }
                                    }
                                }
                            }
                        });
                    }
                }
            }
        });
    }

    fn spawn_rpc_listener(self: &Arc<Self>, rpc_addr: SocketAddr) {
        let mgr = Arc::clone(self);
        tokio::spawn(async move {
            let listener = match TcpListener::bind(rpc_addr).await {
                Ok(l) => { info!("Raft RPC listener on {rpc_addr}"); l }
                Err(e) => { warn!("Raft RPC bind {rpc_addr} failed: {e}"); return; }
            };
            loop {
                match listener.accept().await {
                    Ok((stream, _peer)) => {
                        let m = Arc::clone(&mgr);
                        tokio::spawn(serve_rpc_connection(stream, m));
                    }
                    Err(e) => warn!("Raft RPC accept: {e}"),
                }
            }
        });
    }

    // ── Public API ────────────────────────────────────────────────────────

    /// Leader: propose a write to Raft and wait until a quorum commits it.
    pub async fn propose_write(&self, query: &str) -> Result<(), String> {
        let index = self.raft.propose(query.as_bytes().to_vec())
            .map_err(|e| e.to_string())?;
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().push(PendingWrite { log_index: index, tx });
        tokio::time::timeout(Duration::from_secs(5), rx)
            .await
            .map_err(|_| "replication timeout — quorum not reached".to_string())?
            .map_err(|_| "internal channel dropped".to_string())?
    }

    /// Follower: forward a write to the leader and wait for its ack.
    pub async fn forward_write(&self, query: &str) -> Result<(), String> {
        let leader = self.leader_rpc_addr()
            .ok_or_else(|| "no leader known — try again shortly".to_string())?;
        let msg = RpcMessage::ForwardWrite { from_id: self.node_id.0, query: query.to_string() };
        match send_rpc(&leader, msg).await {
            Ok(Some(RpcMessage::WriteAck { ok: true, .. })) => Ok(()),
            Ok(Some(RpcMessage::WriteAck { ok: false, error: Some(e), .. })) => Err(e),
            Ok(_) => Err("unexpected response from leader".into()),
            Err(e) => Err(format!("forward to leader {leader}: {e}")),
        }
    }

    /// Convenience: route to `propose_write` if leader, `forward_write` if follower.
    pub async fn route_write(&self, query: &str) -> Result<(), String> {
        if self.is_leader() {
            self.propose_write(query).await
        } else {
            self.forward_write(query).await
        }
    }
}

// ── RPC connection handler ────────────────────────────────────────────────

async fn serve_rpc_connection(stream: TcpStream, mgr: Arc<ReplicationManager>) {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    while let Ok(Some(line)) = lines.next_line().await {
        let msg: RpcMessage = match serde_json::from_str(&line) {
            Ok(m) => m,
            Err(e) => { warn!("bad Raft RPC: {e}"); continue; }
        };

        let response = handle_rpc(msg, &mgr).await;

        if let Some(resp) = response {
            if let Ok(mut json) = serde_json::to_vec(&resp) {
                json.push(b'\n');
                let _ = writer.write_all(&json).await;
            }
        }
    }
}

async fn handle_rpc(msg: RpcMessage, mgr: &Arc<ReplicationManager>) -> Option<RpcMessage> {
    match msg {
        RpcMessage::RequestVote(rv) => {
            let req = rv.to_raft();
            let reply = mgr.raft.on_request_vote(req);
            Some(RpcMessage::RequestVoteReply(RvrWire {
                from: mgr.node_id.0,
                term: reply.term,
                vote_granted: reply.vote_granted,
            }))
        }

        RpcMessage::AppendEntries(ae_wire) => {
            // Track who the current leader is (for follower forwarding)
            let leader_id = ae_wire.leader_id;
            if let Some(addr) = mgr.peers.read().await.get(&leader_id) {
                *mgr.leader_addr.lock().unwrap() = Some(addr.clone());
            }

            let prev_commit = mgr.raft.commit_index();
            let ae = ae_wire.to_raft();
            let reply = mgr.raft.on_append_entries(ae);
            let new_commit = mgr.raft.commit_index();

            // Apply any newly committed entries to the local DB
            if new_commit > prev_commit {
                if let Some(atx) = mgr.apply_tx.lock().unwrap().as_ref() {
                    let state = mgr.raft.state.lock().unwrap();
                    for idx in (prev_commit + 1)..=new_commit {
                        if let Some(entry) = state.log.get(idx as usize) {
                            let q = String::from_utf8_lossy(&entry.data).into_owned();
                            let _ = atx.send(ApplyCmd { index: idx, query: q });
                        }
                    }
                }
            }

            Some(RpcMessage::AppendEntriesReply(AerWire {
                from: mgr.node_id.0,
                term: reply.term,
                success: reply.success,
                match_index: reply.match_index,
            }))
        }

        RpcMessage::ForwardWrite { query, .. } => {
            let result = mgr.propose_write(&query).await;
            let (ok, error) = match result {
                Ok(_) => (true, None),
                Err(e) => (false, Some(e)),
            };
            Some(RpcMessage::WriteAck { ok, error })
        }

        RpcMessage::RequestVoteReply(_)
        | RpcMessage::AppendEntriesReply(_)
        | RpcMessage::WriteAck { .. } => None,
    }
}

// ── Network helper ────────────────────────────────────────────────────────

async fn send_rpc(addr: &str, msg: RpcMessage) -> std::io::Result<Option<RpcMessage>> {
    let mut stream = tokio::time::timeout(
        Duration::from_secs(2),
        TcpStream::connect(addr),
    ).await
    .map_err(|_| std::io::Error::new(std::io::ErrorKind::TimedOut, "connect timeout"))??;

    let mut payload = serde_json::to_vec(&msg)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    payload.push(b'\n');
    stream.write_all(&payload).await?;

    let mut reader = BufReader::new(&mut stream);
    let mut line = String::new();
    reader.read_line(&mut line).await?;
    if line.trim().is_empty() {
        return Ok(None);
    }
    let reply: RpcMessage = serde_json::from_str(line.trim())
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    Ok(Some(reply))
}

// ── Apply committed entries to the DB ─────────────────────────────────────

async fn apply_write_to_db(db: &Arc<Database>, query: &str) {
    use crate::sql::ast::Stmt;
    use crate::sql::parser::Parser;
    use crate::types::Value;
    use crate::engine::evaluator::Evaluator;

    let stmts = match Parser::parse_str(query) {
        Ok(s) => s,
        Err(e) => { warn!("replication apply parse error '{query}': {e:?}"); return; }
    };

    for stmt in stmts {
        match stmt {
            Stmt::Put { table, fields } => {
                let tbl_lock = match db.tables.read().unwrap().get(&table).cloned() {
                    Some(t) => t,
                    None => { warn!("replication: table '{table}' not found"); continue; }
                };
                let mut tbl = tbl_lock.write().unwrap();
                let schema_cols: Vec<String> = tbl.schema.columns.iter().map(|c| c.name.clone()).collect();
                let mut row = std::collections::HashMap::new();
                for (i, (_name, expr)) in fields.iter().enumerate() {
                    let col = schema_cols.get(i).map(String::as_str).unwrap_or("_");
                    let val = Evaluator::eval(expr, &crate::types::Row::new(Default::default()))
                        .unwrap_or(Value::Null);
                    row.insert(col.to_string(), val);
                }
                if let Err(e) = tbl.insert(row) {
                    warn!("replication apply PUT error: {e}");
                }
            }
            Stmt::Del { table, filter } => {
                let tbl_lock = match db.tables.read().unwrap().get(&table).cloned() {
                    Some(t) => t,
                    None => continue,
                };
                let mut tbl = tbl_lock.write().unwrap();
                tbl.delete(|row| match &filter {
                    Some(f) => crate::engine::evaluator::Evaluator::matches_filter(f, row).unwrap_or(false),
                    None    => true,
                });
            }
            Stmt::Set { table, fields, filter } => {
                let tbl_lock = match db.tables.read().unwrap().get(&table).cloned() {
                    Some(t) => t,
                    None => continue,
                };
                let mut tbl = tbl_lock.write().unwrap();
                let mut updates = std::collections::HashMap::new();
                for (name, expr) in fields {
                    let val = Evaluator::eval(&expr, &crate::types::Row::new(Default::default()))
                        .unwrap_or(Value::Null);
                    updates.insert(name, val);
                }
                tbl.update(updates, |row| match &filter {
                    Some(f) => Evaluator::matches_filter(f, row).unwrap_or(false),
                    None    => true,
                });
            }
            // DDL and other statements forwarded as-is — apply in single-node executor
            other => {
                debug!("replication: skipping non-DML stmt {other:?}");
            }
        }
    }
}
