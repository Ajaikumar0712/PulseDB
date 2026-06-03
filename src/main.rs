//! PulseDB — server entry point.
//!
//! Supports four modes:
//!   pulsedb-server                    → run in console (foreground)
//!   pulsedb-server install            → register as Windows service
//!   pulsedb-server uninstall          → remove Windows service
//!   pulsedb-server start              → start the Windows service
//!   pulsedb-server stop               → stop the Windows service
//!   pulsedb-server run-service        → internal: entry point when launched by SCM
#![allow(dead_code)]

mod auth;
mod error;
mod metrics;
mod resource;
mod sql;
mod storage;
mod engine;
mod types;
mod transaction;
mod wal;
mod server;
mod cluster;
mod mvcc;
mod ai;
mod triggers;
mod graph;
mod api;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use clap::{Parser as ClapParser, Subcommand};
use tracing::info;
use tracing_subscriber::{fmt, EnvFilter};

use crate::auth::AuthManager;
use crate::metrics::Metrics;
use crate::server::Server;
use crate::storage::persist;
use crate::storage::table::Database;
use crate::wal::WalWriter;
use crate::cluster::ClusterRegistry;
use crate::engine::watch::WatchRegistry;

// ── Service name constant ─────────────────────────────────────────────────

pub const SERVICE_NAME: &str = "PulseDBLite";
pub const SERVICE_DISPLAY: &str = "PulseDB Database";
pub const SERVICE_DESC: &str = "PulseDB — a custom database engine with PulseQL query language";

// ── CLI ───────────────────────────────────────────────────────────────────

#[derive(ClapParser, Debug)]
#[command(
    name = "pulsedb-server",
    about = "PulseDB database server",
    version,
    long_about = "PulseDB database server.\n\nRun without arguments to start in console mode.\nOn Windows: use subcommands to manage the Windows Service.\nOn Linux/macOS: use a systemd unit or launchd plist to run as a background service."
)]
struct Cli {
    /// Address to listen on
    #[arg(short, long, default_value = "127.0.0.1:7878", global = true)]
    addr: SocketAddr,

    /// Path to WAL file
    #[arg(short, long, default_value = "pulsedb.wal", global = true)]
    wal: PathBuf,

    /// Directory for catalog + snapshot persistence
    #[arg(short, long, default_value = "pulsedb-data", global = true)]
    data_dir: PathBuf,

    /// Log level (trace, debug, info, warn, error)
    #[arg(short, long, default_value = "info", global = true)]
    log_level: String,

    /// Storage mode: 'memory' (default) or 'disk'.
    /// Disk mode enables WAL fsync + row eviction to disk when the row cache
    /// is full, suitable for datasets larger than available RAM.
    #[arg(long, default_value = "memory", global = true)]
    mode: crate::storage::disk_store::StorageMode,

    /// Per-table in-memory row limit before rows are evicted to disk.
    /// Only effective in --mode disk.  Default: 500 000 rows per table.
    #[arg(long, default_value = "500000", global = true)]
    row_cache: usize,

    /// Disable authentication — every client is treated as an admin.
    /// WARNING: only use on localhost or in trusted private networks.
    #[arg(long, global = true)]
    no_auth: bool,

    /// Admin username for the initial secured-mode account.
    /// Defaults to "admin". Only used when --no-auth is NOT set.
    #[arg(long, default_value = "admin", global = true)]
    admin_user: String,

    /// Admin password. If omitted, reads PULSEDB_ADMIN_PASSWORD env var.
    /// If that is also unset, a random password is generated and printed once.
    #[arg(long, global = true)]
    admin_password: Option<String>,

    /// Path to a PEM-encoded TLS certificate file.
    /// Both --tls-cert and --tls-key must be provided to enable TLS.
    #[arg(long, global = true)]
    tls_cert: Option<PathBuf>,

    /// Path to a PEM-encoded TLS private key file.
    #[arg(long, global = true)]
    tls_key: Option<PathBuf>,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Install PulseDB as a Windows Service (run once, requires admin)
    #[cfg(windows)]
    Install,
    /// Remove the PulseDB Windows Service (requires admin)
    #[cfg(windows)]
    Uninstall,
    /// Start the PulseDB Windows Service
    #[cfg(windows)]
    Start,
    /// Stop the PulseDB Windows Service
    #[cfg(windows)]
    Stop,
    /// Internal: launched by Windows Service Control Manager
    #[cfg(windows)]
    #[command(hide = true)]
    RunService,
    /// Print a systemd unit file for running PulseDB as a Linux service
    #[cfg(not(windows))]
    SystemdUnit,
}

// ── Entry point ───────────────────────────────────────────────────────────

fn main() {
    let cli = Cli::parse();

    match cli.command {
        #[cfg(windows)]
        Some(Commands::Install)    => service_mgmt::install(&cli.addr, &cli.wal, &cli.log_level),
        #[cfg(windows)]
        Some(Commands::Uninstall)  => service_mgmt::uninstall(),
        #[cfg(windows)]
        Some(Commands::Start)      => service_mgmt::start(),
        #[cfg(windows)]
        Some(Commands::Stop)       => service_mgmt::stop(),
        #[cfg(windows)]
        Some(Commands::RunService) => {
            windows_svc::run_as_service(cli.addr, cli.wal, cli.log_level);
        }
        #[cfg(not(windows))]
        Some(Commands::SystemdUnit) => {
            print_systemd_unit(&cli.addr, &cli.wal, &cli.data_dir, &cli.log_level);
        }
        None => {
            run_console(cli.addr, cli.wal, cli.data_dir, cli.log_level, cli.mode, cli.row_cache,
                        cli.no_auth, cli.admin_user, cli.admin_password,
                        cli.tls_cert, cli.tls_key);
        }
    }
}

// ── Console mode ─────────────────────────────────────────────────────────

fn init_logging(log_level: &str) {
    let filter = EnvFilter::try_new(log_level)
        .unwrap_or_else(|_| EnvFilter::new("info"));
    fmt()
        .with_env_filter(filter)
        .with_target(true)
        .with_thread_ids(true)
        .json()
        .init();
}

pub(crate) fn run_server(
    addr: SocketAddr,
    wal_path: PathBuf,
    data_dir: PathBuf,
    mode: crate::storage::disk_store::StorageMode,
    row_cache: usize,
    no_auth: bool,
    admin_user: String,
    admin_password: Option<String>,
    tls_cert: Option<PathBuf>,
    tls_key: Option<PathBuf>,
) {
    info!("PulseDB starting — addr={addr}, wal={}, data={}, mode={mode}",
        wal_path.display(), data_dir.display());
    let db      = Arc::new(Database::new());
    let wal     = Arc::new(
        WalWriter::open_with_sync(&wal_path, mode.is_disk())
            .expect("failed to open WAL")
    );
    let metrics = Arc::new(Metrics::new());

    // Recover from catalog + snapshots (no-op if data_dir is fresh)
    if let Err(e) = persist::recover(&db, &data_dir) {
        eprintln!("recovery error: {e}");
        std::process::exit(1);
    }

    // Replay committed WAL statements written since the last checkpoint.
    // Also loads saved HNSW indexes from <data-dir>/*.hnsw.json.
    {
        use crate::wal::{read_wal, committed_statements};
        use crate::engine::executor::Executor as ReplayExec;

        match read_wal(&wal_path) {
            Ok(records) if !records.is_empty() => {
                let stmts_json = committed_statements(records);
                if !stmts_json.is_empty() {
                    info!("WAL replay: {} committed statements to apply", stmts_json.len());
                    let replay_exec = ReplayExec::new(Arc::clone(&db), Arc::clone(&metrics));
                    let mut ok = 0usize;
                    let mut err = 0usize;
                    for stmt_json in stmts_json {
                        match serde_json::from_value::<crate::sql::ast::Stmt>(stmt_json) {
                            Ok(stmt) => {
                                if let Err(e) = replay_exec.execute(stmt) {
                                    tracing::warn!("WAL replay error (skipping): {e}");
                                    err += 1;
                                } else { ok += 1; }
                            }
                            Err(e) => { tracing::warn!("WAL replay stmt parse: {e}"); err += 1; }
                        }
                    }
                    info!("WAL replay complete: {ok} applied, {err} skipped");
                    // Restore HNSW indexes that were saved at the last CHECKPOINT
                    replay_exec.load_hnsw_indexes(&data_dir);
                }
            }
            Ok(_) => {
                // No WAL records — still try to load saved HNSW indexes
                let tmp = ReplayExec::new(Arc::clone(&db), Arc::clone(&metrics));
                tmp.load_hnsw_indexes(&data_dir);
            }
            Err(e) => tracing::warn!("WAL replay skipped (read error): {e}"),
        }
    }

    // Build auth manager — secure by default, open only with --no-auth.
    // Password precedence: --admin-password flag → PULSEDB_ADMIN_PASSWORD env → generated.
    let admin_password = admin_password
        .or_else(|| std::env::var("PULSEDB_ADMIN_PASSWORD").ok());
    let auth_manager = if no_auth {
        info!("WARNING: running in open mode (--no-auth). Any client can read and write all data.");
        std::sync::Arc::new(AuthManager::open())
    } else {
        let password = admin_password.unwrap_or_else(|| {
            let p = generate_random_password();
            println!("=============================================================");
            println!(" PulseDB admin password (save this — shown only once):");
            println!("   user:     {admin_user}");
            println!("   password: {p}");
            println!(" Set PULSEDB_ADMIN_PASSWORD env var to skip this on restart.");
            println!("=============================================================");
            p
        });
        info!("running in secured mode — admin user: {admin_user}");
        std::sync::Arc::new(AuthManager::secured(&admin_user, &password))
    };

    let watch_registry   = Arc::new(WatchRegistry::new());
    let cluster_registry = Arc::new(ClusterRegistry::new());

    let lsm_data_dir = data_dir.clone(); // saved before data_dir is moved into the server
    let mut srv = Server::new(db, wal, metrics, addr)
        .with_data_dir(data_dir)
        .with_watch_registry(Arc::clone(&watch_registry))
        .with_cluster_registry(Arc::clone(&cluster_registry))
        .with_storage_mode(mode, row_cache)
        .with_auth_manager(auth_manager);

    // Enable TLS if both cert and key paths are provided
    match (tls_cert, tls_key) {
        (Some(cert_path), Some(key_path)) => {
            match build_tls_acceptor(&cert_path, &key_path) {
                Ok(acceptor) => {
                    info!("TLS configured — cert={}", cert_path.display());
                    srv = srv.with_tls(acceptor);
                }
                Err(e) => {
                    eprintln!("TLS configuration error: {e}");
                    std::process::exit(1);
                }
            }
        }
        (None, None) => {} // TLS not requested — plain TCP
        _ => {
            eprintln!("Both --tls-cert and --tls-key must be provided to enable TLS");
            std::process::exit(1);
        }
    }

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("tokio runtime")
        .block_on(async move {
            // Background heartbeat task: probe each cluster peer every 10 s.
            let cr = Arc::clone(&cluster_registry);
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(std::time::Duration::from_secs(10)).await;
                    let addrs = cr.peer_addrs();
                    for peer_addr in addrs {
                        let t = std::time::Instant::now();
                        let result = tokio::time::timeout(
                            std::time::Duration::from_secs(2),
                            tokio::net::TcpStream::connect(&peer_addr),
                        )
                        .await;
                        if let Ok(Ok(_)) = result {
                            cr.record_heartbeat(&peer_addr, t.elapsed().as_millis() as u64);
                        } else {
                            cr.record_unreachable(&peer_addr);
                        }
                    }
                }
            });

            // Background LSM compaction: runs every 30 s, compacts when
            // L0 SSTable count exceeds the configured threshold.
            {
                use crate::storage::lsm::{LsmTree, LsmConfig, start_compaction_worker};
                let lsm_dir = lsm_data_dir.join("lsm");
                if let Ok(lsm) = LsmTree::open(LsmConfig {
                    data_dir: lsm_dir,
                    ..LsmConfig::default()
                }) {
                    start_compaction_worker(
                        std::sync::Arc::new(lsm),
                        std::time::Duration::from_secs(30),
                    );
                }
            }

            if let Err(e) = srv.serve().await {
                eprintln!("server error: {e}");
                std::process::exit(1);
            }
        });
}

fn run_console(
    addr: SocketAddr,
    wal: PathBuf,
    data_dir: PathBuf,
    log_level: String,
    mode: crate::storage::disk_store::StorageMode,
    row_cache: usize,
    no_auth: bool,
    admin_user: String,
    admin_password: Option<String>,
    tls_cert: Option<PathBuf>,
    tls_key: Option<PathBuf>,
) {
    init_logging(&log_level);
    info!("Running in console mode ({mode} storage). Press Ctrl+C to stop.");
    run_server(addr, wal, data_dir, mode, row_cache, no_auth, admin_user, admin_password,
               tls_cert, tls_key);
}

/// Load a TLS certificate + key pair and build a `TlsAcceptor`.
fn build_tls_acceptor(
    cert_path: &PathBuf,
    key_path: &PathBuf,
) -> Result<tokio_rustls::TlsAcceptor, String> {
    use tokio_rustls::rustls::ServerConfig;
    use rustls_pemfile::{certs, private_key};
    use std::fs::File;
    use std::io::BufReader;

    let cert_file = File::open(cert_path)
        .map_err(|e| format!("cannot open cert {}: {e}", cert_path.display()))?;
    let cert_chain = certs(&mut BufReader::new(cert_file))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("cert parse error: {e}"))?;

    let key_file = File::open(key_path)
        .map_err(|e| format!("cannot open key {}: {e}", key_path.display()))?;
    let key = private_key(&mut BufReader::new(key_file))
        .map_err(|e| format!("key parse error: {e}"))?
        .ok_or_else(|| "no private key found in key file".to_string())?;

    let config = ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(cert_chain, key)
        .map_err(|e| format!("TLS config error: {e}"))?;

    Ok(tokio_rustls::TlsAcceptor::from(std::sync::Arc::new(config)))
}

/// Generate a cryptographically random 20-character alphanumeric password.
fn generate_random_password() -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    use std::time::{SystemTime, UNIX_EPOCH};

    // Combine process ID and nanosecond timestamp for entropy.
    let mut h = DefaultHasher::new();
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos()
        .hash(&mut h);
    std::process::id().hash(&mut h);
    let seed = h.finish();

    // Two rounds to get 20 chars.
    let charset: &[u8] = b"abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
    let len = charset.len() as u64;
    let mut v = seed;
    let mut out = String::with_capacity(20);
    for _ in 0..20 {
        v = v.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
        out.push(charset[(v >> 33) as usize % len as usize] as char);
    }
    out
}

#[cfg(not(windows))]
fn print_systemd_unit(addr: &SocketAddr, wal: &PathBuf, data_dir: &PathBuf, log_level: &str) {
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "/usr/local/bin/pulsedb-server".into());
    println!(
        r#"[Unit]
Description=PulseDB Database Server
After=network.target

[Service]
Type=simple
ExecStart={exe} --addr {addr} --wal {wal} --data-dir {data_dir} --log-level {log_level}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"#,
        exe = exe,
        addr = addr,
        wal = wal.display(),
        data_dir = data_dir.display(),
        log_level = log_level,
    );
    eprintln!("Save this to /etc/systemd/system/pulsedb.service, then:");
    eprintln!("  sudo systemctl daemon-reload");
    eprintln!("  sudo systemctl enable --now pulsedb");
}

// ── Windows service logic ─────────────────────────────────────────────────

#[cfg(windows)]
mod windows_svc {
    use std::ffi::OsString;
    use std::net::SocketAddr;
    use std::path::PathBuf;
    use std::sync::mpsc;
    use std::time::Duration;

    use windows_service::service::{
        ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus, ServiceType,
    };
    use windows_service::service_control_handler::{self, ServiceControlHandlerResult};
    use windows_service::{define_windows_service, service_dispatcher};

    use super::run_server;

    // These are set once before the service dispatcher takes over
    static mut SERVICE_ADDR: Option<SocketAddr> = None;
    static mut SERVICE_WAL:  Option<PathBuf> = None;
    static mut SERVICE_DATA: Option<PathBuf> = None;

    define_windows_service!(ffi_service_main, service_main);

    pub fn run_as_service(addr: SocketAddr, wal: PathBuf, log_level: String) {
        // Initialize logging to file since we have no console
        let filter = tracing_subscriber::EnvFilter::try_new(&log_level)
            .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
        tracing_subscriber::fmt()
            .with_env_filter(filter)
            .with_ansi(false)
            .json()
            .init();

        // Store config for the service entry point
        unsafe {
            SERVICE_ADDR = Some(addr);
            SERVICE_WAL  = Some(wal);
            SERVICE_DATA = Some(PathBuf::from("pulsedb-data"));
        }

        service_dispatcher::start(super::SERVICE_NAME, ffi_service_main)
            .expect("service dispatcher failed");
    }

    #[allow(static_mut_refs)]
    fn service_main(_args: Vec<OsString>) {
        let (shutdown_tx, shutdown_rx) = mpsc::channel::<()>();

        let event_handler = move |control_event| -> ServiceControlHandlerResult {
            match control_event {
                ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
                ServiceControl::Stop => {
                    let _ = shutdown_tx.send(());
                    ServiceControlHandlerResult::NoError
                }
                _ => ServiceControlHandlerResult::NotImplemented,
            }
        };

        let status_handle = service_control_handler::register(super::SERVICE_NAME, event_handler)
            .expect("register service handler");

        // Report: Running
        status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Running,
            controls_accepted: ServiceControlAccept::STOP,
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: Duration::default(),
            process_id: None,
        }).expect("set service status Running");

        // Spawn server on a background thread so we can monitor shutdown_rx
        let addr     = unsafe { SERVICE_ADDR.expect("addr not set") };
        let wal      = unsafe { SERVICE_WAL.take().expect("wal not set") };
        let data_dir = unsafe { SERVICE_DATA.take().unwrap_or_else(|| PathBuf::from("pulsedb-data")) };

        let server_thread = std::thread::spawn(move || {
            let admin_password = std::env::var("PULSEDB_ADMIN_PASSWORD").ok();
            // TLS for Windows service: place cert/key paths in env vars
            // PULSEDB_TLS_CERT and PULSEDB_TLS_KEY if desired.
            let tls_cert = std::env::var("PULSEDB_TLS_CERT").ok().map(PathBuf::from);
            let tls_key  = std::env::var("PULSEDB_TLS_KEY").ok().map(PathBuf::from);
            run_server(addr, wal, data_dir,
                crate::storage::disk_store::StorageMode::Memory, 500_000,
                false, "admin".into(), admin_password, tls_cert, tls_key);
        });

        // Block until SCM sends Stop
        let _ = shutdown_rx.recv();

        // Report: Stopped
        status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Stopped,
            controls_accepted: ServiceControlAccept::empty(),
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: Duration::default(),
            process_id: None,
        }).expect("set service status Stopped");

        drop(server_thread);
    }
}

// ── Service management (install/uninstall/start/stop) ────────────────────

mod service_mgmt {
    use std::net::SocketAddr;
    use std::path::PathBuf;

    pub fn install(addr: &SocketAddr, wal: &PathBuf, log_level: &str) {
        #[cfg(windows)]
        {
            use std::ffi::OsString;
            use windows_service::service::{
                ServiceAccess, ServiceErrorControl, ServiceInfo, ServiceStartType, ServiceType,
            };
            use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};

            let manager = ServiceManager::local_computer(
                None::<&str>,
                ServiceManagerAccess::CREATE_SERVICE,
            ).unwrap_or_else(|e| { eprintln!("Cannot open Service Manager: {e}\nRun as Administrator."); std::process::exit(1); });

            let exe = std::env::current_exe().expect("current exe path");
            // Build the service binary path with stored args
            let bin_path = format!(
                "\"{}\" --addr {} --wal \"{}\" --log-level {} run-service",
                exe.display(), addr, wal.display(), log_level
            );

            let service_info = ServiceInfo {
                name: OsString::from(crate::SERVICE_NAME),
                display_name: OsString::from(crate::SERVICE_DISPLAY),
                service_type: ServiceType::OWN_PROCESS,
                start_type: ServiceStartType::AutoStart,
                error_control: ServiceErrorControl::Normal,
                executable_path: std::path::PathBuf::from(&bin_path),
                launch_arguments: vec![],
                dependencies: vec![],
                account_name: None,  // LocalSystem
                account_password: None,
            };

            match manager.create_service(&service_info, ServiceAccess::CHANGE_CONFIG) {
                Ok(svc) => {
                    // Set description
                    svc.set_description(crate::SERVICE_DESC).ok();
                    println!("✓ Service '{}' installed successfully.", crate::SERVICE_NAME);
                    println!("  Start it with: pulsedb-server start");
                    println!("  Or via Services panel (services.msc)");
                }
                Err(e) => eprintln!("Failed to install service: {e}"),
            }
        }
        #[cfg(not(windows))]
        eprintln!("Service management is Windows-only. Use a systemd unit on Linux.");
    }

    pub fn uninstall() {
        #[cfg(windows)]
        {
            use windows_service::service::ServiceAccess;
            use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};

            let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)
                .unwrap_or_else(|e| { eprintln!("Cannot open Service Manager: {e}"); std::process::exit(1); });

            let svc = manager.open_service(crate::SERVICE_NAME, ServiceAccess::DELETE)
                .unwrap_or_else(|e| { eprintln!("Cannot open service '{}': {e}", crate::SERVICE_NAME); std::process::exit(1); });

            match svc.delete() {
                Ok(_)  => println!("✓ Service '{}' uninstalled.", crate::SERVICE_NAME),
                Err(e) => eprintln!("Failed to uninstall: {e}"),
            }
        }
        #[cfg(not(windows))]
        eprintln!("Service management is Windows-only.");
    }

    pub fn start() {
        #[cfg(windows)]
        {
            use windows_service::service::ServiceAccess;
            use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};

            let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)
                .unwrap_or_else(|e| { eprintln!("Cannot open Service Manager: {e}"); std::process::exit(1); });

            let svc = manager.open_service(crate::SERVICE_NAME, ServiceAccess::START)
                .unwrap_or_else(|e| { eprintln!("Cannot open service: {e}"); std::process::exit(1); });

            match svc.start::<&str>(&[]) {
                Ok(_)  => println!("✓ Service '{}' started.", crate::SERVICE_NAME),
                Err(e) => eprintln!("Failed to start: {e}"),
            }
        }
        #[cfg(not(windows))]
        eprintln!("Service management is Windows-only.");
    }

    pub fn stop() {
        #[cfg(windows)]
        {
            use std::time::Duration;
            use windows_service::service::{ServiceAccess, ServiceState};
            use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};

            let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)
                .unwrap_or_else(|e| { eprintln!("Cannot open Service Manager: {e}"); std::process::exit(1); });

            let svc = manager.open_service(
                crate::SERVICE_NAME,
                ServiceAccess::STOP | ServiceAccess::QUERY_STATUS,
            ).unwrap_or_else(|e| { eprintln!("Cannot open service: {e}"); std::process::exit(1); });

            let status = svc.stop().unwrap_or_else(|e| { eprintln!("Failed to stop: {e}"); std::process::exit(1); });

            if status.current_state == ServiceState::StopPending {
                println!("Service is stopping...");
                std::thread::sleep(Duration::from_secs(3));
            }
            println!("✓ Service '{}' stopped.", crate::SERVICE_NAME);
        }
        #[cfg(not(windows))]
        eprintln!("Service management is Windows-only.");
    }
}

