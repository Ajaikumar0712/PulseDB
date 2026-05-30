//! Disk-backed row store — overflow tier for disk mode.
//!
//! When a table's in-memory row count exceeds `cache_limit`, the oldest rows
//! (by insertion order) are serialised to a per-table append-only file:
//!
//!   `<data_dir>/pages/<table_name>.rows.jsonl`
//!
//! Each line is a JSON-serialised `Row`.  Deleted rows write a tombstone line.
//!
//! # Consistency
//! The disk file is the authority for evicted rows.  The caller ensures that
//! any row written to disk is removed from the in-memory `HashMap<Uuid, Row>`.
//!
//! # Compaction
//! On startup, or when `compact()` is called, the file is rewritten without
//! tombstoned rows (dead rows that are logically deleted).

use std::collections::{HashMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tracing::{debug, info};
use uuid::Uuid;

use crate::error::FlowError;
use crate::types::Row;

// ── Page file record ──────────────────────────────────────────────────────

#[derive(Serialize, Deserialize)]
#[serde(tag = "kind")]
enum PageRecord {
    Row(Row),
    Tombstone { id: Uuid },
}

// ── DiskRowStore ──────────────────────────────────────────────────────────

/// Manages the on-disk overflow file for one table.
pub struct DiskRowStore {
    table: String,
    path: PathBuf,
    /// IDs currently resident on disk (not in memory).
    on_disk: HashMap<Uuid, ()>,
    /// Insertion order for LRU eviction decisions (newest last).
    eviction_queue: VecDeque<Uuid>,
    /// How many rows to keep in memory before evicting.
    pub cache_limit: usize,
}

impl DiskRowStore {
    /// Create (or open) the store for `table` in `data_dir/pages/`.
    pub fn open(table: &str, data_dir: &Path, cache_limit: usize) -> Result<Self, FlowError> {
        let dir = data_dir.join("pages");
        fs::create_dir_all(&dir)
            .map_err(|e| FlowError::Io(format!("create pages dir: {e}")))?;
        let path = dir.join(format!("{table}.rows.jsonl"));
        info!("disk row store: {}", path.display());
        Ok(Self {
            table: table.to_string(),
            path,
            on_disk: HashMap::new(),
            eviction_queue: VecDeque::new(),
            cache_limit,
        })
    }

    /// Track that `id` was inserted into the in-memory table.
    /// Adds it to the eviction queue so it can be paged out when needed.
    pub fn track_insert(&mut self, id: Uuid) {
        self.eviction_queue.push_back(id);
    }

    /// Evict the oldest `count` rows from `mem_rows` to disk.
    /// Returns the list of evicted IDs so the caller can remove them from memory.
    pub fn evict(
        &mut self,
        count: usize,
        mem_rows: &HashMap<Uuid, Row>,
    ) -> Result<Vec<Uuid>, FlowError> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| FlowError::Io(format!("open page file {}: {e}", self.path.display())))?;
        let mut writer = BufWriter::new(file);
        let mut evicted = Vec::new();

        while evicted.len() < count {
            let id = match self.eviction_queue.pop_front() {
                Some(id) => id,
                None => break,
            };
            // Skip IDs that are already on disk or were deleted from memory.
            if self.on_disk.contains_key(&id) || !mem_rows.contains_key(&id) {
                continue;
            }
            let row = &mem_rows[&id];
            let rec = PageRecord::Row(row.clone());
            let line = serde_json::to_string(&rec)
                .map_err(|e| FlowError::Io(format!("serialise row: {e}")))?;
            writeln!(writer, "{line}")
                .map_err(|e| FlowError::Io(format!("write page: {e}")))?;
            self.on_disk.insert(id, ());
            evicted.push(id);
        }

        writer.flush().map_err(|e| FlowError::Io(format!("flush page: {e}")))?;
        debug!(table = %self.table, count = evicted.len(), "evicted rows to disk");
        Ok(evicted)
    }

    /// Load all on-disk rows back into memory (e.g. for a full scan or recovery).
    pub fn load_all(&self) -> Result<Vec<Row>, FlowError> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let file = File::open(&self.path)
            .map_err(|e| FlowError::Io(format!("open page file: {e}")))?;
        let reader = BufReader::new(file);
        let mut live: HashMap<Uuid, Row> = HashMap::new();

        for (i, line) in reader.lines().enumerate() {
            let line = line.map_err(|e| FlowError::Io(format!("read page line {i}: {e}")))?;
            if line.trim().is_empty() { continue; }
            let rec: PageRecord = serde_json::from_str(&line)
                .map_err(|e| FlowError::Io(format!("parse page line {i}: {e}")))?;
            match rec {
                PageRecord::Row(row) => { live.insert(row.id, row); }
                PageRecord::Tombstone { id } => { live.remove(&id); }
            }
        }
        Ok(live.into_values().collect())
    }

    /// Write a tombstone for a deleted row so it's excluded on next load.
    pub fn mark_deleted(&mut self, id: Uuid) -> Result<(), FlowError> {
        if !self.on_disk.contains_key(&id) {
            return Ok(()); // row was never evicted — nothing to do
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| FlowError::Io(format!("open page file: {e}")))?;
        let mut w = BufWriter::new(file);
        let rec = PageRecord::Tombstone { id };
        writeln!(w, "{}", serde_json::to_string(&rec).unwrap())
            .map_err(|e| FlowError::Io(format!("write tombstone: {e}")))?;
        self.on_disk.remove(&id);
        Ok(())
    }

    /// True if `id` is known to reside on disk (not in memory).
    pub fn is_on_disk(&self, id: &Uuid) -> bool {
        self.on_disk.contains_key(id)
    }

    /// Compact the page file: rewrite without tombstones or stale entries.
    pub fn compact(&mut self) -> Result<(), FlowError> {
        let rows = self.load_all()?;
        if rows.is_empty() {
            let _ = fs::remove_file(&self.path);
            self.on_disk.clear();
            return Ok(());
        }
        let tmp = self.path.with_extension("tmp");
        {
            let file = File::create(&tmp)
                .map_err(|e| FlowError::Io(format!("create compact tmp: {e}")))?;
            let mut w = BufWriter::new(file);
            for row in &rows {
                let rec = PageRecord::Row(row.clone());
                writeln!(w, "{}", serde_json::to_string(&rec).unwrap())
                    .map_err(|e| FlowError::Io(format!("write compact: {e}")))?;
            }
        }
        fs::rename(&tmp, &self.path)
            .map_err(|e| FlowError::Io(format!("rename compact: {e}")))?;
        self.on_disk.clear();
        for row in &rows {
            self.on_disk.insert(row.id, ());
        }
        info!(table = %self.table, rows = rows.len(), "disk store compacted");
        Ok(())
    }

    /// How many rows are currently on disk.
    pub fn on_disk_count(&self) -> usize {
        self.on_disk.len()
    }
}

// ── StorageMode ───────────────────────────────────────────────────────────

/// Determines how PulseDB balances memory and disk.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StorageMode {
    /// Default: all data in memory, WAL + snapshots for crash safety.
    Memory,
    /// Disk-first: WAL fsync on every write, rows evicted to disk when
    /// the per-table cache limit is exceeded.  Suitable when total dataset
    /// exceeds available RAM.
    Disk,
}

impl StorageMode {
    pub fn is_disk(&self) -> bool {
        matches!(self, StorageMode::Disk)
    }
}

impl std::str::FromStr for StorageMode {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "memory" | "mem"  => Ok(StorageMode::Memory),
            "disk"            => Ok(StorageMode::Disk),
            other => Err(format!("unknown storage mode '{other}' — use 'memory' or 'disk'")),
        }
    }
}

impl std::fmt::Display for StorageMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StorageMode::Memory => write!(f, "memory"),
            StorageMode::Disk   => write!(f, "disk"),
        }
    }
}
