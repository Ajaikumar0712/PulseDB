// PulseDB library crate — exposes internals for benchmarks and integration tests.
#![allow(dead_code)]

pub mod auth;
pub mod cluster;
pub mod engine;
pub mod error;
pub mod metrics;
pub mod mvcc;
pub mod sql;
pub mod storage;
pub mod transaction;
pub mod types;
pub mod wal;

// Internal-only modules not exposed to external crates
mod ai;
mod api;
mod graph;
mod resource;
mod triggers;
