# PulseDB

> **A high-performance database engine built in Rust with its own query language — PulseQL.**

[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/Ajaikumar0712/PulseDB)
[![License](https://img.shields.io/badge/license-BUSL--1.1-orange)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20Docker-lightgrey)](https://github.com/Ajaikumar0712/PulseDB/releases)
[![Built with Rust](https://img.shields.io/badge/built%20with-Rust-orange)](https://www.rust-lang.org)

PulseDB is a self-contained, production-ready database engine that stores data in-memory with WAL-backed durability, communicates over TCP using JSON, and is queried with **PulseQL** — a purpose-built, SQL-inspired query language.

**Made by Ajaikumar · [ForNexus](https://fornexus.tech)**

---

## At a Glance

| Feature | Description |
| --- | --- |
| **PulseQL** | Custom query language — clean, readable, not SQL |
| **In-memory + WAL** | Fast reads/writes with Write-Ahead Log durability |
| **Joins** | INNER, LEFT, RIGHT joins across tables in one query |
| **Aggregation** | GROUP BY with COUNT, SUM, AVG, MIN, MAX and HAVING |
| **Fuzzy Search** | Trigram-based text similarity search (`~`) |
| **Vector Search** | Cosine similarity search with HNSW index (`SIMILAR`) |
| **Streaming** | Real-time push subscriptions with `WATCH` |
| **Transactions** | `BEGIN` / `COMMIT` / `ROLLBACK` with MVCC isolation |
| **Security** | Argon2id passwords, per-user RBAC, auth required by default |
| **TLS** | Encrypted transport via `--tls-cert` / `--tls-key`; REPL `--tls` flag |
| **WAL Encryption** | AES-256-GCM at-rest encryption via `PULSEDB_WAL_KEY` |
| **REST API** | Auto-generated HTTP endpoints per table with API key auth |
| **Rate Limiting** | 100 req/s per IP on REST endpoints; HTTP 429 on exceed |
| **ORM** | Python, JavaScript/TypeScript, and Go ORMs in `clients/` |
| **Migration** | `migrate.py` — import from PostgreSQL, MySQL, SQLite, MongoDB |
| **Clustering** | Raft consensus, peer heartbeats, FNV-1a shard routing |
| **Triggers** | Event-driven PulseQL queries (admin-only, depth-limited) |
| **Graph Queries** | `GRAPH MATCH` — node-edge-node traversal across three tables |
| **Time Travel** | `AS OF` / `VERSION` historical snapshots |
| **AI Search** | Hash-based lexical search (`AI SEARCH`) |
| **Windows Service** | Runs as a background service that starts with Windows |
| **Linux / systemd** | Ready-to-use systemd unit via `systemd-unit` subcommand |
| **Docker** | Multi-stage image — single node or 3-node cluster |
| **MSI Installer** | One-click install on Windows |
| **Benchmarks** | Criterion (Rust) + Python comparison vs PostgreSQL, MongoDB, Redis, Qdrant |

---

## Quick Start

### 1 · Install

Download **`pulsedb-1.0.0-x86_64.msi`** from the [releases page](https://github.com/Ajaikumar0712/PulseDB/releases) and double-click to install.

Both binaries are placed in `C:\Program Files\PulseDB\bin\` and added to your system `PATH`.

### 2 · Start the Server

```powershell
pulsedb-server
```

On first start a random admin password is generated — **save it**:

```text
=============================================================
 PulseDB admin password (save this — shown only once):
   user:     admin
   password: xK7mQpRt9nVw3bYs2cZj
 Set PULSEDB_ADMIN_PASSWORD env var to skip this on restart.
=============================================================
[INFO] PulseDB 1.0.0 listening on 127.0.0.1:7878
```

On every subsequent start supply the password via env var:

```powershell
$env:PULSEDB_ADMIN_PASSWORD = "xK7mQpRt9nVw3bYs2cZj"
pulsedb-server
```

### 3 · Connect and Query

```powershell
pulsedb-repl
```

```sql
AUTH admin 'xK7mQpRt9nVw3bYs2cZj'

MAKE TABLE users (id int PRIMARY KEY, name text, age int)

PUT users { id: 1, name: "Alice", age: 30 }
PUT users { id: 2, name: "Bob",   age: 25 }

GET users
GET users WHERE age >= 28 ORDER BY name ASC
```

---

## Table of Contents

1. [Installation](#installation)
   - [MSI Installer (Windows)](#option-a--msi-installer-windows)
   - [Build from Source](#option-b--build-from-source)
   - [Docker](#option-c--docker)
2. [Running the Server](#running-the-server)
3. [Windows Service](#windows-service)
4. [Linux Service (systemd)](#linux-service-systemd)
5. [TLS — Encrypted Transport](#tls--encrypted-transport)
6. [WAL Encryption — Data at Rest](#wal-encryption--data-at-rest)
7. [Connecting with the REPL](#connecting-with-the-repl)
8. [Client SDKs](#client-sdks)
   - [Python](#python)
   - [Go](#go)
   - [JavaScript](#javascript)
9. [ORM](#orm)
   - [Python ORM](#python-orm)
   - [JavaScript ORM](#javascript-orm)
   - [Go ORM](#go-orm)
10. [Migration Tool](#migration-tool)
11. [PulseQL Language Reference](#pulseql-language-reference)
    - [Data Types](#data-types)
    - [Table Management](#table-management)
    - [Writing Data](#writing-data)
    - [Reading Data](#reading-data)
    - [Joins](#joins)
    - [Aggregation & GROUP BY](#aggregation--group-by)
    - [Fuzzy Search](#fuzzy-search)
    - [Vector Similarity Search](#vector-similarity-search)
    - [Streaming Watch Queries](#streaming-watch-queries)
    - [Transactions](#transactions)
    - [REST API](#rest-api)
    - [Security & User Management](#security--user-management)
    - [Cluster Commands](#cluster-commands)
    - [Triggers](#triggers)
    - [Graph Queries](#graph-queries)
    - [Time Travel Queries](#time-travel-queries)
    - [AI Search](#ai-search)
    - [Admin Commands](#admin-commands)
    - [Resource Configuration](#resource-configuration)
12. [Expressions & Operators](#expressions--operators)
13. [Response Format](#response-format)
14. [Metrics](#metrics)
15. [Benchmarks](#benchmarks)
16. [Architecture](#architecture)
17. [Quick Reference Card](#quick-reference-card)
18. [Troubleshooting](#troubleshooting)
19. [License & Pricing](#license--pricing)

---

## Installation

### Option A — MSI Installer (Windows)

1. Download `pulsedb-1.0.0-x86_64.msi` from the [releases page](https://github.com/Ajaikumar0712/PulseDB/releases)
2. Double-click and follow the installer wizard
3. Accept the BUSL-1.1 license agreement
4. Keep "Add to PATH" checked (recommended)
5. Click **Install**

| File | Location |
| --- | --- |
| `pulsedb-server.exe` | `C:\Program Files\PulseDB\bin\` |
| `pulsedb-repl.exe` | `C:\Program Files\PulseDB\bin\` |
| `LICENSE` | `C:\Program Files\PulseDB\` |
| `PulseDB_Documentation.docx` | `C:\Program Files\PulseDB\` |

To uninstall: **Settings → Apps → PulseDB → Uninstall**

---

### Option B — Build from Source

**Requirements:** Rust 1.70+ — install from [rustup.rs](https://rustup.rs)

```powershell
git clone https://github.com/Ajaikumar0712/PulseDB
cd PulseDB

cargo build --release   # optimised build
cargo test              # run the test suite
```

| Binary | Path |
| --- | --- |
| `pulsedb-server.exe` | `target\release\pulsedb-server.exe` |
| `pulsedb-repl.exe` | `target\release\pulsedb-repl.exe` |

---

### Option C — Docker

```bash
cp .env.example .env
# Edit .env → set PULSEDB_ADMIN_PASSWORD=your-strong-password

docker compose up -d
docker exec -it pulsedb pulsedb-repl
```

Port 7878 is on the **internal Docker network only** — not exposed to the host. To allow host access during development add `ports: ["127.0.0.1:7878:7878"]` to `docker-compose.yml`.

Data persists in the named Docker volume `pulsedb-data`. A 3-node Raft cluster config is included (commented) in `docker-compose.yml`.

```bash
docker build -t pulsedb/pulsedb .
docker run -p 127.0.0.1:7878:7878 -v pulsedb-data:/var/lib/pulsedb pulsedb/pulsedb
```

---

## Running the Server

```powershell
pulsedb-server
```

Default address: `127.0.0.1:7878`. Logs print as JSON. Press `Ctrl+C` to stop.

### Server Flags

| Flag | Short | Default | Description |
| --- | --- | --- | --- |
| `--addr <HOST:PORT>` | `-a` | `127.0.0.1:7878` | Address and port to listen on |
| `--wal <PATH>` | `-w` | `pulsedb.wal` | Path to the Write-Ahead Log file |
| `--data-dir <PATH>` | `-d` | `pulsedb-data` | Directory for catalog and disk snapshots |
| `--log-level <LEVEL>` | `-l` | `info` | Verbosity: `trace` `debug` `info` `warn` `error` |
| `--mode <MODE>` | | `memory` | Storage mode: `memory` or `disk` |
| `--row-cache <N>` | | `500000` | Per-table row limit before disk eviction (disk mode) |
| `--admin-user <NAME>` | | `admin` | Username of the initial admin account |
| `--admin-password <PWD>` | | *(auto-generated)* | Admin password — also read from `PULSEDB_ADMIN_PASSWORD` |
| `--no-auth` | | *(off)* | Disable authentication (dev / localhost only) |
| `--tls-cert <PATH>` | | *(off)* | PEM certificate — enables TLS when combined with `--tls-key` |
| `--tls-key <PATH>` | | *(off)* | PEM private key |

**Examples:**

```powershell
$env:PULSEDB_ADMIN_PASSWORD = "my-strong-password"
pulsedb-server

pulsedb-server --no-auth                               # local dev
pulsedb-server --mode disk --row-cache 100000          # disk mode
pulsedb-server --tls-cert cert.pem --tls-key key.pem  # TLS
pulsedb-server --addr 0.0.0.0:7878                     # all interfaces
```

---

## Windows Service

```powershell
pulsedb-server install    # register (run as Administrator)
pulsedb-server start
pulsedb-repl              # connect from any terminal
pulsedb-server stop
pulsedb-server uninstall
```

Manage via Services panel: `Win+R → services.msc → "PulseDB Database"`

| Command | Requires Admin |
| --- | :-: |
| `pulsedb-server install` | ✅ |
| `pulsedb-server uninstall` | ✅ |
| `pulsedb-server start` | ✅ |
| `pulsedb-server stop` | ✅ |

---

## Linux Service (systemd)

```bash
pulsedb-server --addr 0.0.0.0:7878 --data-dir /var/lib/pulsedb systemd-unit \
  | sudo tee /etc/systemd/system/pulsedb.service

sudo systemctl daemon-reload
sudo systemctl enable --now pulsedb

pulsedb-repl --addr 127.0.0.1:7878
```

The generated unit runs the server as a `pulsedb` system user with `Restart=on-failure`.

---

## TLS — Encrypted Transport

Enable TLS to encrypt all traffic — required for any network that isn't localhost.

```bash
# Generate a self-signed certificate (development)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"

# Start server with TLS
pulsedb-server --tls-cert cert.pem --tls-key key.pem
```

```powershell
# Self-signed cert — dev / localhost only
pulsedb-repl --tls --tls-no-verify

# CA-signed cert — production
pulsedb-repl --tls --addr myserver.example.com:7878
```

| REPL Flag | Description |
| --- | --- |
| `--tls` | Enable TLS on the connection |
| `--tls-no-verify` | Skip certificate verification (self-signed certs only) |

> **SDK clients:** Native TLS support for Python/Go/JavaScript SDKs is on the roadmap. Until then, connect through a TLS-terminating reverse proxy (nginx, Caddy) for non-localhost connections.

---

## WAL Encryption — Data at Rest

Encrypt WAL files with AES-256-GCM by setting `PULSEDB_WAL_KEY`:

```bash
export PULSEDB_WAL_KEY="$(openssl rand -hex 32)"   # Linux / macOS
$env:PULSEDB_WAL_KEY = (openssl rand -hex 32)       # Windows PowerShell

pulsedb-server
```

**Important:**

- The key is never stored by PulseDB — supply it on every start
- Store it in a secrets manager (AWS Secrets Manager, HashiCorp Vault, Azure Key Vault)
- Existing unencrypted WAL lines are auto-detected and read without decryption
- New records written after the key is set will be encrypted
- Losing the key means losing access to all encrypted WAL data

---

## Connecting with the REPL

```powershell
pulsedb-repl                              # local server
pulsedb-repl --addr 192.168.1.10:7878    # remote
pulsedb-repl --tls --addr server:7878    # remote with TLS
```

| Input | Action |
| --- | --- |
| Any PulseQL statement | Execute and print result |
| `help` or `\help` | Show command reference |
| `exit`, `quit`, or `\q` | Disconnect |
| `Ctrl+C` / `Ctrl+D` | Disconnect |

---

## Client SDKs

Official clients live in `clients/`. Each wraps the TCP + JSON protocol.

> **Security:** SDK connections are plain TCP. Only connect over localhost or a trusted private network, or route through a TLS-terminating proxy. Native TLS support is on the roadmap.

---

### Python

```bash
git clone https://github.com/Ajaikumar0712/PulseDB
pip install ./PulseDB/clients/python
# pip install pulsedb  ← once published on PyPI
```

```python
import os
from pulsedb import PulseDB

with PulseDB.connect("127.0.0.1", 7878) as db:
    db.auth(os.environ["PULSEDB_USER"], os.environ["PULSEDB_PASSWORD"])
    db.query("MAKE TABLE users (id int PRIMARY KEY, name text, age int)")
    db.query('PUT users { id: 1, name: "Alice", age: 30 }')
    result = db.query("GET users WHERE age >= 28")
    for row in result:
        print(row.id, row.name, row.age)
```

| Method | Description |
| --- | --- |
| `PulseDB.connect(host, port)` | Open a connection |
| `db.auth(user, password)` | Authenticate the session |
| `db.query(q)` | Run any PulseQL string; returns `Result` |
| `db.close()` | Close the connection |
| `result.rows` | List of `Row` objects |
| `row["col"]` / `row.col` | Column value by name |
| `row.as_dict()` | Row as a plain `dict` |

---

### Go

```bash
go get github.com/Ajaikumar0712/PulseDB/clients/go
```

```go
import (
    "fmt"
    "os"
    pulsedb "github.com/Ajaikumar0712/PulseDB/clients/go"
)

c, _ := pulsedb.Connect("127.0.0.1:7878")
defer c.Close()
c.Auth(os.Getenv("PULSEDB_USER"), os.Getenv("PULSEDB_PASSWORD"))
c.Query(`MAKE TABLE users (id int PRIMARY KEY, name text)`)
c.Query(`PUT users { id: 1, name: "Alice" }`)
result, _ := c.Query("GET users")
for _, row := range result.Rows {
    fmt.Println(row.Get("id"), row.Get("name"))
}
```

`Client` is thread-safe — share a single connection across goroutines.

---

### JavaScript

```bash
# Copy clients/javascript/index.js into your project
```

```js
const { PulseDB } = require('./clients/javascript');

const db = await PulseDB.connect({ host: '127.0.0.1', port: 7878 });
await db.auth(process.env.PULSEDB_USER, process.env.PULSEDB_PASSWORD);
await db.query(`MAKE TABLE users (id int PRIMARY KEY, name text)`);
await db.query(`PUT users { id: 1, name: "Alice" }`);
const result = await db.query('GET users');
for (const row of result) { console.log(row.id, row.name); }
db.close();
```

---

## ORM

Declarative model layers on top of the raw TCP clients — no raw PulseQL strings required. Full examples in `clients/*/example_orm.*`.

---

### Python ORM

```python
from pulsedb.orm import connect, Model, IntField, TextField, FloatField, BoolField, VectorField

db = connect("127.0.0.1", 7878)

class User(Model):
    class Meta:
        db    = db
        table = "users"

    id     = IntField(primary_key=True)
    name   = TextField()
    age    = IntField()
    active = BoolField(default=True)
    score  = FloatField(default=0.0)

# Schema
User.create_table()
User.create_index("age")

# Write
alice = User.create(id=1, name="Alice", age=30)
alice.update(score=0.95)

with db.transaction():
    User.create(id=2, name="Bob",   age=25)
    User.create(id=3, name="Carol", age=35)

# Read — full QuerySet API
adults = User.filter(age__gte=18, active=True).order_by("-score").limit(10).all()
user   = User.get(id=1)
first  = User.filter(active=True).order_by("age").first()
ids    = User.filter(id__in=[1, 2, 3]).all()

# Update / delete
User.filter(id=2).update(active=False)
User.filter(active=False).delete()

# Vector + fuzzy search
User.similar("embedding", [0.9, 0.1, 0.2], k=10)
User.fuzzy("name", "alic", limit=5)
```

**QuerySet lookup suffixes:**

| Suffix | SQL equivalent |
| --- | --- |
| `field=value` | `field = value` |
| `field__gt=v` | `field > v` |
| `field__gte=v` | `field >= v` |
| `field__lt=v` | `field < v` |
| `field__lte=v` | `field <= v` |
| `field__ne=v` | `field != v` |
| `field__in=[a,b]` | `(field=a OR field=b)` |

**Field types:** `IntField` · `FloatField` · `TextField` · `BoolField` · `VectorField` · `JsonField`

---

### JavaScript ORM

Full TypeScript definitions included (`clients/javascript/orm.d.ts`).

```js
const { defineModel, DataTypes, withTransaction } = require('./clients/javascript/orm');

const User = defineModel('users', {
  id:     { type: DataTypes.INT,   primaryKey: true },
  name:   { type: DataTypes.TEXT },
  age:    { type: DataTypes.INT },
  active: { type: DataTypes.BOOL,  defaultValue: true },
  score:  { type: DataTypes.FLOAT, defaultValue: 0.0 },
}, { db });

await User.createTable();
await User.create({ id: 1, name: 'Alice', age: 30 });

// Query
const adults = await User.findAll({ where: { age: { gte: 18 } }, orderBy: '-score', limit: 10 });
const alice  = await User.findByPk(1);
const first  = await User.findOne({ where: { active: true }, orderBy: 'age' });

// Update / delete
await User.update({ active: false }, { where: { age: { lt: 18 } } });
await User.destroy({ where: { id: 99 } });

// Vector + fuzzy
await User.similar('embedding', [0.9, 0.1, 0.2], { limit: 10 });
await User.fuzzy('name', 'alic', { limit: 5 });

// QuerySet chaining
await User.where({ active: true }).orderBy('-score').limit(5).all();

// Transaction
await withTransaction(db, async () => {
  await User.bulkCreate([{ id: 2, name: 'Bob' }, { id: 3, name: 'Carol' }]);
});
```

---

### Go ORM

Struct tags — no code generation required.

```go
type User struct {
    ID     int     `pulsedb:"id,primary_key"`
    Name   string  `pulsedb:"name"`
    Age    int     `pulsedb:"age"`
    Active bool    `pulsedb:"active"`
    Score  float64 `pulsedb:"score"`
}

client, _ := pulsedb.Connect("127.0.0.1:7878")
orm := pulsedb.NewORM(client)

orm.CreateTable(&User{})
orm.Create(&User{ID: 1, Name: "Alice", Age: 30, Active: true})

var users []User
orm.Q(&User{}).Where("age >= 18").Where("active = true").OrderBy("score", true).Limit(10).Find(&users)

var alice User
orm.FindByPK(&alice, 1)

orm.Q(&User{}).Where("id = 1").Update(map[string]interface{}{"score": 0.95})
orm.Q(&User{}).Where("active = false").Delete()

orm.Transaction(func(o *pulsedb.ORM) error {
    return o.Create(&User{ID: 2, Name: "Bob", Age: 25})
})

// Vector + fuzzy
orm.Q(&User{}).Similar("embedding", []float64{0.9, 0.1, 0.2}, 10)
orm.Q(&User{}).Fuzzy("name", "alic", 20)
```

---

## Migration Tool

Import tables, rows, and indexes from an existing database into PulseDB with one command.

**Supported sources:** PostgreSQL · MySQL · SQLite (no extra install) · MongoDB

```bash
# Install only the driver you need
pip install psycopg2-binary   # PostgreSQL
pip install pymysql           # MySQL
pip install pymongo           # MongoDB
# SQLite uses stdlib — nothing to install
```

```bash
# Preview — no data written
python tools/migrate.py postgres://postgres:pass@localhost/mydb --dry-run

# Real migration
python tools/migrate.py postgres://postgres:pass@localhost/mydb --no-auth

# Migrate specific tables
python tools/migrate.py sqlite:///myapp.db --tables users orders products

# MySQL / MongoDB
python tools/migrate.py mysql://root:pass@localhost/shop --batch 5000
python tools/migrate.py mongodb://localhost/analytics

# Remote PulseDB target
python tools/migrate.py postgres://... --target 192.168.1.10:7878
```

**What happens:**

1. Connects to the source database
2. Discovers all tables (or only the ones in `--tables`)
3. Maps SQL types → PulseQL types automatically:

   | SQL | PulseQL |
   | --- | --- |
   | `int`, `bigint`, `serial` | `int` |
   | `float`, `double`, `decimal` | `float` |
   | `varchar`, `text`, `uuid` | `text` |
   | `boolean` | `bool` |
   | `json`, `jsonb` | `json` |
   | `bytea`, `blob` | `blob` |
   | `timestamp`, `date` | `text` |

4. Creates tables in PulseDB (`MAKE TABLE`)
5. Copies rows in batches using transactions (default 1000 rows/batch, set with `--batch`)
6. Creates indexes (`MAKE INDEX`)
7. Shows a live progress bar and final summary

```text
  users  (100,000 rows)
  [████████████████████████████████████████] 100,000/100,000 100.0%
  ✓ 100,000 rows  4.2s  23,809 rows/s

═══════════════════════════════════════════════
  Migration complete
  Tables : 3      Rows : 542,000
  Time   : 18.4s  Rate : 29,456 rows/s
═══════════════════════════════════════════════
```

**Flags:**

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

## Admin Dashboard

A real-time web UI — no npm, no extra pip installs, pure Python stdlib.

```bash
python tools/admin/server.py --no-auth
# Open http://127.0.0.1:8080
```

```bash
python tools/admin/server.py --port 9090
python tools/admin/server.py --pulsedb 192.168.1.10:7878
python tools/admin/server.py --user admin --password secret
python tools/admin/server.py --interval 1    # poll every 1 second
```

**Dashboard pages:**

| Page | What you see |
| --- | --- |
| **Overview** | Uptime, TPS (live), total queries, WAL records, rows inserted/updated/deleted, transaction counts, latency p50/p95/p99/max bars, 60-second rolling TPS chart |
| **Tables** | All tables in the database |
| **Queries** | Currently executing queries with elapsed time and **Kill** button |
| **Cluster** | Peer nodes, reachability, round-trip latency |
| **Users** | All user accounts and roles |
| **Console** | Run any PulseQL query — `Ctrl+Enter` to execute, raw JSON response shown |
| **Config** | Runtime limits (max connections, timeouts, memory caps) |

**Features:** auto-refresh every 2 seconds · live Canvas TPS chart · Kill query button · Checkpoint button · dark theme (GitHub/Vercel style) · no build step

---

## PulseQL Language Reference

PulseQL is **not SQL** — different keywords, designed to be readable and unambiguous.

- Statements can be separated by `;`
- Identifiers are case-insensitive
- String values use double quotes: `"hello"`
- Passwords use single quotes: `'secret'`

---

### Data Types

| Type | Description | Example |
| --- | --- | --- |
| `int` | 64-bit signed integer | `42`, `-10` |
| `float` | 64-bit floating point | `3.14`, `-0.5` |
| `text` | UTF-8 string | `"hello world"` |
| `bool` | Boolean | `true`, `false` |
| `json` | Any JSON value | `{"key": "val"}` |
| `blob` | Raw binary data | — |
| `vector` | Dense f32 vector for similarity search | `[0.1, 0.9, 0.3]` |
| `null` | Absent value | `null` |

---

### Table Management

```sql
MAKE TABLE users (id int PRIMARY KEY, name text, age int, active bool)
MAKE INDEX ON users (age)
DROP TABLE users
SHOW TABLES
```

---

### Writing Data

```sql
-- PUT — insert or replace a row (upsert)
PUT users { id: 1, name: "Alice", age: 30, active: true }
PUT users { id: 2, name: "Bob",   age: 25, active: false }

-- SET — update specific fields on matching rows
SET users { age: 31 } WHERE id = 1
SET users { active: false } WHERE age < 18
SET products { price: 0.0 }          -- no WHERE = update all rows

-- DEL — delete rows
DEL users WHERE id = 2
DEL users WHERE active = false
DEL users                             -- no WHERE = clear the table
```

---

### Reading Data

```sql
GET users
GET users WHERE active = true
GET users WHERE age >= 25 AND active = true
GET users ORDER BY age DESC LIMIT 10
GET users WHERE active = true TIMEOUT "5s"
```

**TIMEOUT formats:** `"500ms"` · `"5s"` · `"2m"` — must be a quoted string.

---

### Joins

```sql
GET users INNER JOIN orders ON users.id = orders.user_id
GET users LEFT  JOIN orders ON users.id = orders.user_id
GET users INNER JOIN orders ON users.id = orders.user_id
    WHERE orders.total > 20.0
    ORDER BY orders.total DESC
    LIMIT 5
```

| Join Type | Returns |
| --- | --- |
| `INNER JOIN` | Rows matching in **both** tables |
| `LEFT JOIN` | All left rows; `null` for unmatched right |
| `RIGHT JOIN` | All right rows; `null` for unmatched left |

---

### Aggregation & GROUP BY

```sql
GET orders GROUP BY country COUNT(*) AS cnt SUM(total) AS revenue
GET orders GROUP BY country SUM(total) AS revenue HAVING cnt > 1
GET orders GROUP BY user_id AVG(total) AS avg ORDER BY avg DESC
```

**Functions:** `COUNT(*)` · `COUNT(col)` · `SUM(col)` · `AVG(col)` · `MIN(col)` · `MAX(col)`

---

### Fuzzy Search

```sql
FIND users WHERE name ~ "alic"           -- finds "Alice" even with typo
FIND products WHERE name ~ "widge" LIMIT 5
GET users WHERE name ~ "alice"           -- ~ also works inside GET
```

---

### Vector Similarity Search

```sql
MAKE TABLE items (id int PRIMARY KEY, label text, embedding vector)

PUT items { id: 1, label: "cat", embedding: [0.9, 0.1, 0.0] }
PUT items { id: 2, label: "dog", embedding: [0.8, 0.2, 0.1] }

SIMILAR items ON embedding TO [0.85, 0.15, 0.05] LIMIT 10
```

Results include an `_score` column (0.0–1.0). Backed by an **HNSW** index. `ON <column>` can be omitted when the table has exactly one `vector` column.

---

### Streaming Watch Queries

```sql
WATCH users WHERE active = true     -- opens push subscription
UNWATCH 1                           -- cancel subscription
```

Push events (one JSON line per change):

```json
{"status":"watch","id":1,"op":"insert","row":{"id":4,"name":"Dave","active":true}}
{"status":"watch","id":1,"op":"update","row":{"id":2,"name":"Bob","active":true}}
{"status":"watch","id":1,"op":"delete","row":{"id":3,"name":"Carol","active":false}}
```

---

### Transactions

```sql
BEGIN

PUT users { id: 10, name: "Dave", age: 28 }
SET users { age: 29 } WHERE id = 10
DEL users WHERE id = 5

COMMIT     -- apply all changes atomically
ROLLBACK   -- discard everything since BEGIN
```

PulseDB uses **MVCC** — readers never block writers and writers never block readers.

---

### REST API

```sql
API GENERATE FOR users     -- starts HTTP server, returns port + API key
API STOP FOR users
SHOW APIS
```

Response from `API GENERATE FOR`:

```text
REST API for `users` running at http://127.0.0.1:7879/api/users
API key: a3f9c2b1d4e8... (include as: Authorization: Bearer a3f9c2b1d4e8...)
```

**Endpoints:**

| Method | Path | Action |
| --- | --- | --- |
| `GET` | `/api/<table>` | Fetch all rows |
| `POST` | `/api/<table>` | Insert a row |
| `GET` | `/api/<table>/<id>` | Fetch a single row |
| `PUT` | `/api/<table>/<id>` | Update row fields |
| `DELETE` | `/api/<table>/<id>` | Delete a row |

All requests require `Authorization: Bearer <api_key>`. Wrong or missing key → **401**. Exceeding 100 req/s per IP → **429**.

```powershell
$key = "a3f9c2b1d4e8..."
$h   = @{ Authorization = "Bearer $key" }
Invoke-RestMethod -Uri "http://127.0.0.1:7879/api/users" -Headers $h
```

---

### Security & User Management

Authentication is **required by default**. Passwords use **Argon2id** (memory-hard, GPU-resistant).

```sql
AUTH alice 'secret123'

CREATE USER bob PASSWORD 'hunter2'
CREATE ADMIN USER carol PASSWORD 'admin-pass'
DROP USER bob

GRANT select ON users TO bob
GRANT all ON * TO carol
REVOKE insert ON orders FROM bob

SHOW USERS
```

**GRANT operations:** `select` · `insert` · `update` · `delete` · `all`

> Pass `--no-auth` to start the server in open mode (dev / localhost only).

---

### Cluster Commands

```sql
CLUSTER JOIN "192.168.1.20:7878"
CLUSTER PART "192.168.1.20:7878"
CLUSTER STATUS

CLUSTER SHARD ASSIGN orders SHARDS 4 NODES "n1:7878", "n2:7878"
CLUSTER SHARD STATUS
CLUSTER SHARD DROP orders
```

---

### Triggers

> **Admin only.** Recursion capped at depth 5.

```sql
TRIGGER log_insert WHEN PUT users  DO PUT audit { action: "user_added" }
TRIGGER bust_cache WHEN SET orders DO DEL order_cache

DROP TRIGGER log_insert
SHOW TRIGGERS
```

---

### Graph Queries

```sql
GRAPH MATCH (a:people) -[rel:follows]-> (b:people)
    [WHERE <condition>]
    [LIMIT <n>]
```

The edge table must have `from_id` and `to_id` columns. Default LIMIT 100, maximum 10,000. SELECT permission is checked on all three tables.

```sql
MAKE TABLE people  (id int PRIMARY KEY, name text)
MAKE TABLE follows (id int PRIMARY KEY, from_id int, to_id int)

GRAPH MATCH (a:people) -[rel:follows]-> (b:people)
    WHERE a.name = "Alice"
    LIMIT 10
```

---

### Time Travel Queries

Requires SELECT permission — same check as a regular GET.

```sql
GET orders AS OF "2024-06-01T00:00:00Z" WHERE country = "US"
GET users VERSION 42
```

---

### AI Search

Hash-based lexical search — **not** semantic / synonym-aware.

```sql
AI SEARCH products "noise cancelling headphones" LIMIT 5
```

> For semantic search, store model-generated embeddings in a `vector` column and use `SIMILAR`.

---

### Admin Commands

```sql
EXPLAIN GET users WHERE age > 25 ORDER BY age DESC LIMIT 10
CHECKPOINT                  -- flush all data to disk
SHOW RUNNING QUERIES
KILL QUERY 42
METRICS
```

---

### Resource Configuration

```sql
CONFIG SET max_connections 200
CONFIG SET max_rows_per_query 50000
CONFIG SET default_timeout_ms 10000
CONFIG SET max_memory_mb 4096

SHOW CONFIG
```

---

## Expressions & Operators

### Comparison

| Operator | Meaning |
| --- | --- |
| `=` | Equal |
| `!=` or `<>` | Not equal |
| `<` / `<=` / `>` / `>=` | Numeric comparison |
| `~` | Fuzzy text match (trigram similarity) |

### Logical

`AND` · `OR` · `NOT`

### Arithmetic

`+` · `-` · `*` · `/`

```sql
GET users WHERE age >= 18 AND age <= 65 AND active = true
GET users WHERE name = "Alice" OR name = "Bob"
GET products WHERE price * 1.2 < 30.0
```

---

## Response Format

All communication uses **newline-terminated JSON**.

**Rows:**

```json
{
  "status": "ok",
  "result": {
    "Rows": {
      "columns": ["id", "name", "age"],
      "rows": [[1, "Alice", 30], [2, "Bob", 25]]
    }
  }
}
```

**Write:**

```json
{"status":"ok","result":{"Ok":{"message":"1 row inserted","elapsed_ms":0}}}
```

**Error:**

```json
{"status":"error","message":"table 'users' does not exist"}
```

---

## Metrics

```sql
METRICS
```

```text
=== PulseDB Metrics ===
Uptime:                    5m 32s
Queries total:             47
Queries errored:           1
Rows inserted:             20
Rows updated:              3
Rows deleted:              2
Transactions committed:    4
Transactions rolled back:  0
WAL records written:       35

Latency (ms):
  Min:   0
  Avg:   1.2
  P95:   4
  P99:   9
  Max:   12
```

---

## Benchmarks

### Rust internal benchmarks (Criterion)

12 benchmark groups covering every major operation:

```bash
cargo bench                          # all groups
cargo bench -- insert                # single group
cargo bench -- --save-baseline main  # save baseline
cargo bench -- --baseline main       # diff against saved

# HTML report: target/criterion/report/index.html
```

| Group | Scales |
| --- | --- |
| `insert` | 1K → 1M rows |
| `point_lookup` | 10K / 100K / 1M dataset |
| `range_scan` | 10% of 10K / 100K / 1M |
| `full_scan` | Filter scan, 10K / 100K / 500K |
| `aggregation` | GROUP BY + COUNT + AVG |
| `order_limit` | ORDER BY + LIMIT 100 |
| `fuzzy_search` | Trigram `~` operator |
| `vector_search` | HNSW 128-dim cosine, k=10 |
| `transaction` | 1 / 10 / 50 / 100 ops per tx |
| `parser` | 8 query types, lex + parse only |
| `mixed_80r_20w` | 80% reads / 20% writes |
| `delete` | DEL WHERE 50% of rows |

### Comparison benchmarks (Python)

Compare PulseDB against PostgreSQL, MongoDB, Redis, and Qdrant:

```bash
pip install psycopg2-binary pymongo redis qdrant-client

cd benchmarks

# Quick run (100K rows, 100 concurrent)
python run_all.py --skip-errors

# Full run (1M rows, 1000 concurrent)
python run_all.py --rows 1000000 --concurrency 1000 --skip-errors

# 10M rows
python run_all.py --rows 10000000 --concurrency 1000 --skip-errors

# Single database
python run_all.py --dbs pulsedb

# Vector search comparison (PulseDB vs Qdrant)
python run_all.py --dbs pulsedb qdrant --vec-rows 100000
```

**Operations measured:** INSERT · POINT LOOKUP · RANGE SCAN · FULL SCAN · AGGREGATION · ORDER BY LIMIT · FUZZY SEARCH · VECTOR SEARCH · CONCURRENT TPS

All results include **TPS + p50 / p95 / p99 latency**. JSON output saved to `benchmarks/results/` for CI diffing.

---

## Architecture

```mermaid
flowchart TD
    subgraph CLIENTS["CLIENT APPLICATIONS"]
        CLI["pulsedb-repl"]
        SDK["Python / Go / JS + ORMs"]
        REST["REST API clients"]
        ADM["Admin Dashboard"]
        MIG["Migration Tool"]
    end

    CLIENTS -->|TCP — plain or TLS| SERVER

    subgraph SERVER["PulseDB SERVER"]
        direction TB
        subgraph QE["QUERY ENGINE"]
            P[Parser] --> PL[Planner] --> EX[Executor]
        end
        subgraph TL["TRANSACTION LAYER — MVCC + WAL"]
        end
        subgraph SE["STORAGE ENGINE"]
            MEM[In-Memory Tables] --- IDX[B-tree Indexes]
        end
        subgraph BG["BACKGROUND SERVICES"]
            WAL[WAL Writer]
            SNAP[Snapshot Manager]
            WATCH[Watch Engine]
            RAFT[Cluster / Raft]
        end
        QE --> TL --> SE
        SE -.-> BG
    end
```

### Layer Breakdown

| Layer | Components | Responsibility |
| --- | --- | --- |
| **Query Engine** | `lexer` → `parser` → `planner` → `executor` | Parse PulseQL, build AST, cost-estimate plan, execute |
| **Transaction Layer** | `transaction.rs`, `mvcc.rs`, `wal.rs` | MVCC snapshot isolation, BEGIN/COMMIT/ROLLBACK, WAL |
| **Storage Engine** | `table.rs`, `columnar.rs`, `buffer_pool.rs`, `lsm.rs` | In-memory B-tree tables, columnar compression, LRU cache, LSM compaction |
| **Background Services** | `wal.rs`, `persist.rs`, `watch.rs`, `cluster/` | WAL flush, disk snapshots, push subscriptions, Raft consensus |

### Repository Structure

```text
src/                          — Rust server source
├── main.rs                   — CLI, TLS setup, Windows Service
├── server.rs                 — Async TCP listener, TLS handshake
├── repl.rs                   — Interactive REPL client
├── auth.rs                   — Argon2id passwords, RBAC
├── wal.rs                    — Append-only WAL + AES-256-GCM encryption
├── engine/                   — Query engine (executor, planner, evaluator, HNSW, watch)
├── storage/                  — Storage engine (B-tree tables, columnar, LSM, buffer pool)
├── cluster/                  — Raft consensus, shard routing, heartbeats
├── triggers/                 — Event-driven trigger store
├── api/                      — Auto-generated REST endpoints, API key auth, rate limiting
├── graph/                    — GRAPH MATCH traversal
└── ai/                       — FNV-1a hash projection for AI SEARCH

clients/
├── python/pulsedb/
│   ├── __init__.py           — Raw TCP client
│   └── orm.py                — Python ORM (Model, Field, QuerySet)
├── go/
│   ├── pulsedb.go            — Raw TCP client
│   └── orm.go                — Go ORM (struct tags, NewORM, QueryBuilder)
└── javascript/
    ├── index.js              — Raw async TCP client
    ├── orm.js                — JavaScript ORM (defineModel, DataTypes)
    └── orm.d.ts              — TypeScript definitions

tools/
├── migrate.py                — Migration CLI (PostgreSQL / MySQL / SQLite / MongoDB)
└── admin/
    ├── server.py             — Admin dashboard HTTP server (Python stdlib)
    └── index.html            — Dashboard frontend (vanilla HTML/CSS/JS, dark theme)

benchmarks/
├── run_all.py                — Master runner (all databases, comparison table + JSON)
├── common.py                 — Shared timing / reporting utilities
└── compare/
    ├── pulsedb_bench.py      — PulseDB benchmark
    ├── postgres_bench.py     — PostgreSQL benchmark
    ├── mongodb_bench.py      — MongoDB benchmark
    ├── redis_bench.py        — Redis benchmark
    └── qdrant_bench.py       — Qdrant vector search benchmark

benches/
└── pulseql.rs                — Rust Criterion benchmarks (12 groups, up to 1M rows)
```

### Columnar Compression

| Encoding | Applied when |
| --- | --- |
| **Bitmap** | Boolean columns |
| **Dictionary** | Low-cardinality text (≤ 256 distinct values per batch) |
| **RLE** | Long runs of repeated values |
| **Raw** | Everything else |

### Query Planner

Uses equi-depth histograms (16 buckets/column) + NDV (Number of Distinct Values) to estimate filter selectivity. Falls back to full scan automatically if an index scan would touch more rows.

---

## Quick Reference Card

```sql
-- ── Tables ────────────────────────────────────────────────────────────
MAKE TABLE t (id int PRIMARY KEY, name text, val float, emb vector)
MAKE INDEX ON t (name)
DROP TABLE t
SHOW TABLES

-- ── Write ─────────────────────────────────────────────────────────────
PUT t { id: 1, name: "Alice", val: 3.14, emb: [0.9, 0.1, 0.0] }
SET t { val: 9.99 } WHERE id = 1
DEL t WHERE id = 1

-- ── Read ──────────────────────────────────────────────────────────────
GET t
GET t WHERE val > 5.0 AND name = "Alice"
GET t ORDER BY val DESC LIMIT 10
GET t TIMEOUT "5s"

-- ── Search ────────────────────────────────────────────────────────────
FIND t WHERE name ~ "alic"
SIMILAR t ON emb TO [0.85, 0.15, 0.05] LIMIT 5
AI SEARCH t "search phrase" LIMIT 5

-- ── Joins ─────────────────────────────────────────────────────────────
GET users INNER JOIN orders ON users.id = orders.user_id
GET users LEFT  JOIN orders ON users.id = orders.user_id WHERE orders.total > 10

-- ── Aggregation ───────────────────────────────────────────────────────
GET orders GROUP BY country COUNT(*) AS cnt SUM(total) AS revenue
GET orders GROUP BY user_id AVG(total) AS avg ORDER BY avg DESC

-- ── Streaming ─────────────────────────────────────────────────────────
WATCH t WHERE val > 5.0
UNWATCH 1

-- ── Transactions ──────────────────────────────────────────────────────
BEGIN
  PUT t { id: 10, name: "Test" }
  SET t { val: 1.0 } WHERE id = 10
COMMIT
ROLLBACK

-- ── Time travel ───────────────────────────────────────────────────────
GET t AS OF "2024-06-01T00:00:00Z"
GET t VERSION 42

-- ── Triggers ──────────────────────────────────────────────────────────
TRIGGER log_insert WHEN PUT t DO PUT audit { action: "insert" }
DROP TRIGGER log_insert
SHOW TRIGGERS

-- ── Graph queries ─────────────────────────────────────────────────────
GRAPH MATCH (a:people) -[rel:follows]-> (b:people) WHERE a.name = "Alice" LIMIT 10

-- ── REST API ──────────────────────────────────────────────────────────
API GENERATE FOR users
API STOP FOR users
SHOW APIS

-- ── Security ──────────────────────────────────────────────────────────
AUTH alice 'secret123'
CREATE USER bob PASSWORD 'pass'
CREATE ADMIN USER carol PASSWORD 'admin'
GRANT select ON users TO bob
GRANT all ON * TO carol
REVOKE insert ON orders FROM bob
DROP USER bob
SHOW USERS

-- ── Cluster ───────────────────────────────────────────────────────────
CLUSTER JOIN "192.168.1.20:7878"
CLUSTER PART "192.168.1.20:7878"
CLUSTER STATUS
CLUSTER SHARD ASSIGN orders SHARDS 4 NODES "n1:7878", "n2:7878"
CLUSTER SHARD STATUS
CLUSTER SHARD DROP orders

-- ── Resource limits ───────────────────────────────────────────────────
CONFIG SET max_connections 200
CONFIG SET default_timeout_ms 10000
SHOW CONFIG

-- ── Admin ─────────────────────────────────────────────────────────────
EXPLAIN GET t WHERE val > 5
CHECKPOINT
METRICS
SHOW RUNNING QUERIES
KILL QUERY 7
```

---

## Troubleshooting

### "address already in use" — server won't start

```powershell
pulsedb-server --addr 127.0.0.1:7879
```

### "connection refused" — REPL can't connect

The server isn't running. Start it first:

```powershell
pulsedb-server --no-auth
```

### "not authenticated" or "permission denied"

Every connection must authenticate before running queries:

```sql
AUTH admin 'your-password'
```

Lost the admin password? Restart with a new one:

```powershell
$env:PULSEDB_ADMIN_PASSWORD = "new-strong-password"
pulsedb-server
```

For local dev, skip auth entirely: `pulsedb-server --no-auth`

### "table does not exist"

```sql
MAKE TABLE users (id int PRIMARY KEY, name text)
```

### PUT silently replaces an existing row

`PUT` is an upsert by design. Use `SET` to update specific fields only.

### Query timeout errors

```sql
GET big_table LIMIT 1000 TIMEOUT "30s"
CONFIG SET default_timeout_ms 30000
```

### "GRAPH MATCH LIMIT cannot exceed 10000"

Reduce the `LIMIT` clause on your `GRAPH MATCH` query.

### WAL file growing very large

```sql
CHECKPOINT
```

### Windows Service — "access denied"

Right-click PowerShell → "Run as Administrator".

### TLS — "connection refused" after enabling TLS

```powershell
pulsedb-repl --tls --tls-no-verify   # self-signed cert
pulsedb-repl --tls                    # CA-signed cert
```

### Migration — "psycopg2 not found"

```bash
pip install psycopg2-binary
```

### Admin dashboard — "cannot reach PulseDB"

Make sure the server is running: `pulsedb-server --no-auth`, then retry `python tools/admin/server.py --no-auth`.

---

## License & Pricing

PulseDB is licensed under the **Business Source License 1.1 (BUSL-1.1)**.

| Use case | Allowed |
| --- | :-: |
| Personal use, learning, experimentation | ✅ Free |
| Open-source non-commercial projects | ✅ Free |
| 30-day production trial | ✅ Free |
| Commercial production use (after trial) | ❌ Paid license required |

On **March 11, 2030**, PulseDB automatically converts to the **Apache License 2.0** — free for everyone, forever.

### Commercial Licensing

| Tier | Price | Includes |
| --- | --- | --- |
| **Starter** | $500 / year | 1 server, 1 company |
| **Business** | $2,000 / year | Up to 5 servers |
| **Enterprise** | Contact us | Unlimited servers, priority support, SLA |

**Contact:**

- **Email:** [pulsedb.license@gmail.com](mailto:pulsedb.license@gmail.com)
- **Website:** <https://fornexus.tech>

See the full [LICENSE](LICENSE) file for complete terms.

---

*PulseDB is a product of [ForNexus](https://fornexus.tech), developed by Ajaikumar, with contribution from [Gayu-0707](https://github.com/Gayu-0707).*
