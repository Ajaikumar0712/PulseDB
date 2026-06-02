# PulseDB Tools

Standalone tools shipped with PulseDB — no extra install for the server, just Python 3.8+.

---

## Migration Tool

Import tables, rows, and indexes from PostgreSQL, MySQL, SQLite, or MongoDB into PulseDB.

### Install source drivers (only for your source database)

```bash
pip install psycopg2-binary   # PostgreSQL
pip install pymysql           # MySQL
pip install pymongo           # MongoDB
# SQLite is built into Python — no install needed
```

### Usage

```bash
# PostgreSQL
python tools/migrate.py postgres://postgres:pass@localhost/mydb

# MySQL
python tools/migrate.py mysql://root:pass@localhost/shop

# SQLite
python tools/migrate.py sqlite:///path/to/myapp.db

# MongoDB
python tools/migrate.py mongodb://localhost/analytics

# Options
python tools/migrate.py postgres://... --target 192.168.1.10:7878
python tools/migrate.py postgres://... --tables users orders products
python tools/migrate.py postgres://... --batch 5000
python tools/migrate.py postgres://... --dry-run        # preview without writing
python tools/migrate.py postgres://... --skip-existing
python tools/migrate.py postgres://... --no-auth
```

### What it does

1. Connects to the source database
2. Discovers all tables (or the ones you specify with `--tables`)
3. Maps SQL types to PulseQL types:

   | SQL type | PulseQL |
   | --- | --- |
   | `int`, `bigint`, `serial` | `int` |
   | `float`, `double`, `decimal` | `float` |
   | `varchar`, `text`, `char`, `uuid` | `text` |
   | `boolean` | `bool` |
   | `json`, `jsonb` | `json` |
   | `bytea`, `blob` | `blob` |
   | `timestamp`, `date` | `text` |

4. Creates the tables in PulseDB (`MAKE TABLE`)
5. Copies rows in batches of 1000 (configurable with `--batch`) using transactions
6. Creates indexes (`MAKE INDEX`)
7. Prints a live progress bar and final summary

### Authentication

```bash
# PulseDB admin credentials
python tools/migrate.py postgres://... --pulsedb-user admin --pulsedb-password secret

# Via environment variables
PULSEDB_USER=admin PULSEDB_PASSWORD=secret python tools/migrate.py postgres://...

# Skip auth (if server started with --no-auth)
python tools/migrate.py postgres://... --no-auth
```

### Options reference

| Flag | Description |
| --- | --- |
| `--target HOST:PORT` | PulseDB address (default: `127.0.0.1:7878`) |
| `--tables T1 T2` | Migrate only specific tables |
| `--batch N` | Rows per transaction batch (default: 1000) |
| `--dry-run` | Preview schema + first row, no writes |
| `--skip-existing` | Skip tables that already exist in PulseDB |
| `--no-auth` | Connect to PulseDB without authenticating |
| `--pulsedb-user` | PulseDB admin username |
| `--pulsedb-password` | PulseDB admin password |

---

## Typical workflow

```bash
# 1. Start PulseDB
.\target\release\pulsedb-server.exe --no-auth

# 2. Preview the migration (no data written)
python tools/migrate.py postgres://postgres:pass@localhost/mydb --dry-run

# 3. Run the migration
python tools/migrate.py postgres://postgres:pass@localhost/mydb --no-auth
```
