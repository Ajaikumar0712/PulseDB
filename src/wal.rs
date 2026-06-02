//! Write-Ahead Log (WAL) — guarantees crash safety for PulseDB.
//!
//! Design:
//! - Every write (INSERT / UPDATE / DELETE) is appended to the WAL before
//!   being applied to the in-memory table.
//! - On crash recovery, the WAL is replayed in order to reconstruct the
//!   last committed state.
//! - Records are newline-delimited JSON for debuggability.
//! - BEGIN / COMMIT / ROLLBACK fence transaction boundaries.

use std::fs::{File, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;
use std::sync::Mutex;

use aes_gcm::{
    aead::{Aead, KeyInit},
    Aes256Gcm, Key, Nonce,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tracing::{debug, info, warn};
use uuid::Uuid;

use crate::error::FlowError;
use crate::types::Value;

// ── WAL record types ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum WalRecord {
    /// Start of an explicit transaction.
    Begin { tx_id: u64 },
    /// INSERT: a new row was inserted.
    Insert {
        tx_id: u64,
        table: String,
        row_id: Uuid,
        fields: std::collections::HashMap<String, Value>,
        timestamp: DateTime<Utc>,
    },
    /// UPDATE: specific fields were updated on an existing row.
    Update {
        tx_id: u64,
        table: String,
        row_id: Uuid,
        updates: std::collections::HashMap<String, Value>,
        timestamp: DateTime<Utc>,
    },
    /// DELETE: a row was soft-deleted.
    Delete {
        tx_id: u64,
        table: String,
        row_id: Uuid,
        timestamp: DateTime<Utc>,
    },
    /// Successful commit of a transaction.
    Commit { tx_id: u64, timestamp: DateTime<Utc> },
    /// Transaction was rolled back — discard all records with this tx_id.
    Rollback { tx_id: u64 },
    /// DDL: a table was created.
    CreateTable { tx_id: u64, table: String },
    /// DDL: a table was dropped.
    DropTable { tx_id: u64, table: String },
    /// Statement-level record — stores the full serialized Stmt for crash replay.
    /// Replaces the empty-fields placeholders. On recovery, committed statements
    /// are deserialized and re-executed through the executor.
    Statement {
        tx_id: u64,
        stmt_json: serde_json::Value,
        timestamp: DateTime<Utc>,
    },
}

impl WalRecord {
    pub fn tx_id(&self) -> u64 {
        match self {
            WalRecord::Begin { tx_id } => *tx_id,
            WalRecord::Insert { tx_id, .. } => *tx_id,
            WalRecord::Update { tx_id, .. } => *tx_id,
            WalRecord::Delete { tx_id, .. } => *tx_id,
            WalRecord::Commit { tx_id, .. } => *tx_id,
            WalRecord::Rollback { tx_id } => *tx_id,
            WalRecord::CreateTable { tx_id, .. } => *tx_id,
            WalRecord::DropTable { tx_id, .. } => *tx_id,
            WalRecord::Statement { tx_id, .. } => *tx_id,
        }
    }
}

/// Extract all committed Stmt records from WAL records in order.
/// Skips rolled-back transactions and returns only mutation statements
/// (Put, Set, Del, MakeTable, DropTable) from committed transactions.
pub fn committed_statements(
    records: Vec<WalRecord>,
) -> Vec<serde_json::Value> {
    use std::collections::{HashMap, HashSet};

    let mut buffered: HashMap<u64, Vec<serde_json::Value>> = HashMap::new();
    let mut committed: HashSet<u64> = HashSet::new();
    let mut rolled_back: HashSet<u64> = HashSet::new();

    for rec in records {
        match rec {
            WalRecord::Begin { tx_id } => {
                buffered.entry(tx_id).or_default();
            }
            WalRecord::Statement { tx_id, stmt_json, .. } => {
                buffered.entry(tx_id).or_default().push(stmt_json);
            }
            WalRecord::CreateTable { tx_id, ref table } => {
                // Legacy DDL records (pre-Statement variant) — reconstruct minimal stmt JSON
                let json = serde_json::json!({ "MakeTable": { "name": table, "columns": [] } });
                buffered.entry(tx_id).or_default().push(json);
            }
            WalRecord::DropTable { tx_id, ref table } => {
                let json = serde_json::json!({ "DropTable": { "name": table } });
                buffered.entry(tx_id).or_default().push(json);
            }
            WalRecord::Commit { tx_id, .. } => {
                committed.insert(tx_id);
            }
            WalRecord::Rollback { tx_id } => {
                rolled_back.insert(tx_id);
                buffered.remove(&tx_id);
            }
            _ => {}
        }
    }

    // Sort committed tx_ids in ascending order so statements are replayed in
    // the same order they were originally executed (not random HashMap order).
    let mut sorted_committed: Vec<u64> = committed.into_iter().collect();
    sorted_committed.sort_unstable();

    sorted_committed
        .into_iter()
        .filter_map(|tx_id| buffered.remove(&tx_id))
        .flatten()
        .collect()
}

// ── Wal writer ────────────────────────────────────────────────────────────

/// Thread-safe append-only WAL writer.
#[allow(dead_code)]
pub struct WalWriter {
    writer: Mutex<BufWriter<File>>,
    pub path: std::path::PathBuf,
    /// When `true`, calls `sync_all()` after every append (disk-mode durability).
    sync_writes: bool,
    /// Optional AES-256-GCM key for at-rest encryption.
    /// Set via `PULSEDB_WAL_KEY` env var (64 hex chars = 32 bytes).
    enc_key: Option<[u8; 32]>,
}

impl WalWriter {
    /// Open (or create) the WAL file at `path`.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, FlowError> {
        Self::open_with_sync(path, false)
    }

    /// Open with explicit sync mode.  Use `sync = true` in disk mode.
    /// Reads `PULSEDB_WAL_KEY` env var (64 hex chars) to enable AES-256-GCM encryption.
    pub fn open_with_sync(path: impl AsRef<Path>, sync: bool) -> Result<Self, FlowError> {
        let path = path.as_ref().to_path_buf();
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| FlowError::Wal(format!("cannot open WAL at {}: {e}", path.display())))?;

        let enc_key = load_wal_key();
        if enc_key.is_some() {
            info!("WAL opened at `{}` (sync={}, encryption=AES-256-GCM)", path.display(), sync);
        } else {
            info!("WAL opened at `{}` (sync={}, encryption=off)", path.display(), sync);
        }

        Ok(Self {
            writer: Mutex::new(BufWriter::new(file)),
            path,
            sync_writes: sync,
            enc_key,
        })
    }

    /// Append a single record to the WAL.
    /// If an encryption key is configured, the record is AES-256-GCM encrypted:
    ///   line format: `enc:<hex(nonce || ciphertext)>`
    /// Otherwise the record is written as plain JSON.
    pub fn append(&self, record: &WalRecord) -> Result<(), FlowError> {
        let json = serde_json::to_string(record)
            .map_err(|e| FlowError::Wal(format!("serialization failed: {e}")))?;

        let line = if let Some(key_bytes) = &self.enc_key {
            encrypt_record(&json, key_bytes)
                .map_err(|e| FlowError::Wal(format!("WAL encryption failed: {e}")))?
        } else {
            json
        };

        let mut w = self.writer.lock().unwrap();
        writeln!(w, "{line}")
            .map_err(|e| FlowError::Wal(format!("write failed: {e}")))?;
        w.flush()
            .map_err(|e| FlowError::Wal(format!("flush failed: {e}")))?;

        if self.sync_writes {
            w.get_ref().sync_all()
                .map_err(|e| FlowError::Wal(format!("fsync failed: {e}")))?;
        }

        debug!("WAL appended op={}", record_op_name(record));
        Ok(())
    }
}

fn record_op_name(r: &WalRecord) -> &'static str {
    match r {
        WalRecord::Begin { .. }       => "BEGIN",
        WalRecord::Insert { .. }      => "INSERT",
        WalRecord::Update { .. }      => "UPDATE",
        WalRecord::Delete { .. }      => "DELETE",
        WalRecord::Commit { .. }      => "COMMIT",
        WalRecord::Rollback { .. }    => "ROLLBACK",
        WalRecord::CreateTable { .. } => "CREATE_TABLE",
        WalRecord::DropTable { .. }   => "DROP_TABLE",
        WalRecord::Statement { .. }   => "STATEMENT",
    }
}

// ── Wal reader / recovery ─────────────────────────────────────────────────

/// Read all WAL records from `path` for crash recovery.
/// Automatically decrypts lines that begin with `enc:` using the `PULSEDB_WAL_KEY` env var.
pub fn read_wal(path: impl AsRef<Path>) -> Result<Vec<WalRecord>, FlowError> {
    let path = path.as_ref();
    if !path.exists() {
        return Ok(Vec::new());
    }

    let enc_key = load_wal_key();

    let file = File::open(path)
        .map_err(|e| FlowError::Wal(format!("cannot read WAL: {e}")))?;
    let mut reader = BufReader::new(file);
    let mut contents = String::new();
    reader
        .read_to_string(&mut contents)
        .map_err(|e| FlowError::Wal(format!("WAL read error: {e}")))?;

    let mut records = Vec::new();
    for (line_no, line) in contents.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        // Decrypt if the line is encrypted
        let json = if line.starts_with("enc:") {
            match &enc_key {
                Some(key) => match decrypt_record(line, key) {
                    Ok(s) => s,
                    Err(e) => {
                        warn!("WAL line {line_no}: decryption failed — skipping: {e}");
                        continue;
                    }
                },
                None => {
                    warn!("WAL line {line_no}: encrypted record but PULSEDB_WAL_KEY not set — skipping");
                    continue;
                }
            }
        } else {
            line.to_string()
        };

        match serde_json::from_str::<WalRecord>(&json) {
            Ok(r) => records.push(r),
            Err(e) => {
                warn!("WAL line {line_no}: parse error — skipping: {e}");
            }
        }
    }
    Ok(records)
}

// ── Encryption helpers ────────────────────────────────────────────────────

/// Read and decode `PULSEDB_WAL_KEY` env var (64 hex chars = 32 bytes).
fn load_wal_key() -> Option<[u8; 32]> {
    let hex = std::env::var("PULSEDB_WAL_KEY").ok()?;
    let hex = hex.trim();
    if hex.len() != 64 {
        warn!("PULSEDB_WAL_KEY must be 64 hex characters (32 bytes) — WAL encryption disabled");
        return None;
    }
    let mut key = [0u8; 32];
    for (i, chunk) in hex.as_bytes().chunks(2).enumerate() {
        let byte_str = std::str::from_utf8(chunk).ok()?;
        key[i] = u8::from_str_radix(byte_str, 16).ok()?;
    }
    Some(key)
}

/// Encrypt a plaintext JSON string.
/// Output format: `enc:<hex(12-byte nonce || ciphertext)>`
fn encrypt_record(plaintext: &str, key_bytes: &[u8; 32]) -> Result<String, String> {
    use aes_gcm::aead::OsRng;
    use aes_gcm::aead::rand_core::RngCore;

    let key = Key::<Aes256Gcm>::from_slice(key_bytes);
    let cipher = Aes256Gcm::new(key);

    let mut nonce_bytes = [0u8; 12];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext.as_bytes())
        .map_err(|e| e.to_string())?;

    // Encode as hex: nonce || ciphertext
    let mut combined = nonce_bytes.to_vec();
    combined.extend_from_slice(&ciphertext);
    let hex = combined.iter().map(|b| format!("{:02x}", b)).collect::<String>();
    Ok(format!("enc:{hex}"))
}

/// Decrypt a `enc:<hex>` line back to JSON.
fn decrypt_record(line: &str, key_bytes: &[u8; 32]) -> Result<String, String> {
    let hex = line.strip_prefix("enc:").ok_or("missing enc: prefix")?;
    let bytes: Vec<u8> = hex
        .as_bytes()
        .chunks(2)
        .map(|c| {
            let s = std::str::from_utf8(c).map_err(|e| e.to_string())?;
            u8::from_str_radix(s, 16).map_err(|e| e.to_string())
        })
        .collect::<Result<_, _>>()?;

    if bytes.len() < 12 {
        return Err("ciphertext too short".into());
    }
    let (nonce_bytes, ciphertext) = bytes.split_at(12);
    let key = Key::<Aes256Gcm>::from_slice(key_bytes);
    let cipher = Aes256Gcm::new(key);
    let nonce = Nonce::from_slice(nonce_bytes);
    let plaintext = cipher
        .decrypt(nonce, ciphertext)
        .map_err(|e| e.to_string())?;
    String::from_utf8(plaintext).map_err(|e| e.to_string())
}

/// Given a complete WAL record list, return only those records belonging to
/// committed transactions (discard incomplete and rolled-back transactions).
pub fn committed_records(records: Vec<WalRecord>) -> Vec<WalRecord> {
    use std::collections::HashSet;

    // Collect all committed and rolled-back tx_ids
    let committed: HashSet<u64> = records
        .iter()
        .filter_map(|r| if matches!(r, WalRecord::Commit { .. }) { Some(r.tx_id()) } else { None })
        .collect();

    let rolled_back: HashSet<u64> = records
        .iter()
        .filter_map(|r| if matches!(r, WalRecord::Rollback { .. }) { Some(r.tx_id()) } else { None })
        .collect();

    records
        .into_iter()
        .filter(|r| {
            // Keep only data records from committed transactions
            let is_data = !matches!(r, WalRecord::Begin { .. } | WalRecord::Commit { .. } | WalRecord::Rollback { .. });
            is_data && committed.contains(&r.tx_id()) && !rolled_back.contains(&r.tx_id())
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use tempfile::NamedTempFile;

    #[test]
    fn test_write_and_read_wal() {
        let tmp = NamedTempFile::new().unwrap();
        let wal = WalWriter::open(tmp.path()).unwrap();

        let rec = WalRecord::Insert {
            tx_id: 1,
            table: "users".into(),
            row_id: Uuid::new_v4(),
            fields: HashMap::new(),
            timestamp: Utc::now(),
        };
        wal.append(&rec).unwrap();
        wal.append(&WalRecord::Commit { tx_id: 1, timestamp: Utc::now() }).unwrap();

        let all = read_wal(tmp.path()).unwrap();
        assert_eq!(all.len(), 2);
    }

    #[test]
    fn test_committed_records_filters_rollback() {
        let records = vec![
            WalRecord::Insert {
                tx_id: 1,
                table: "t".into(),
                row_id: Uuid::new_v4(),
                fields: HashMap::new(),
                timestamp: Utc::now(),
            },
            WalRecord::Rollback { tx_id: 1 },
            WalRecord::Insert {
                tx_id: 2,
                table: "t".into(),
                row_id: Uuid::new_v4(),
                fields: HashMap::new(),
                timestamp: Utc::now(),
            },
            WalRecord::Commit { tx_id: 2, timestamp: Utc::now() },
        ];
        let committed = committed_records(records);
        assert_eq!(committed.len(), 1);
        assert_eq!(committed[0].tx_id(), 2);
    }
}
