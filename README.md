# PulseDB

> A fast, self-contained database engine built in Rust.

[![Version](https://img.shields.io/badge/version-1.0.0-blue?style=flat-square)](https://github.com/Ajaikumar0712/PulseDB/releases)
[![License](https://img.shields.io/badge/license-BUSL--1.1-orange?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20Docker-lightgrey?style=flat-square)](https://github.com/Ajaikumar0712/PulseDB/releases)
[![Rust](https://img.shields.io/badge/built%20with-Rust-orange?style=flat-square)](https://www.rust-lang.org)
[![Tests](https://img.shields.io/badge/tests-104%20passing-brightgreen?style=flat-square)](https://github.com/Ajaikumar0712/PulseDB)

[Quick Start](#quick-start) · [Docs](#pulseql-language-reference) · [SDKs](#client-sdks) · [Benchmarks](#benchmarks) · [Releases](https://github.com/Ajaikumar0712/PulseDB/releases)

---

PulseDB stores data in-memory with WAL-backed crash recovery, speaks a clean query language called **PulseQL**, and ships everything you need — vector search, fuzzy text, streaming subscriptions, clustering, and REST APIs — as a single binary with no external dependencies.

```sql
MAKE TABLE products (id int PRIMARY KEY, name text, price float, embedding vector)

PUT products { id: 1, name: "Widget",  price: 9.99,  embedding: [0.9, 0.1, 0.2] }
PUT products { id: 2, name: "Gadget",  price: 24.99, embedding: [0.1, 0.8, 0.4] }

GET products WHERE price < 20.0 ORDER BY price ASC
SIMILAR products ON embedding TO [0.88, 0.12, 0.22] LIMIT 5
FIND products WHERE name ~ "widg"
```

---

## Features

### Core

| | |
| --- | --- |
| **PulseQL** | Purpose-built query language — readable, unambiguous, not SQL |
| **In-memory + WAL** | Sub-millisecond reads and writes; crash-safe with WAL and snapshot recovery |
| **Transactions** | `BEGIN` / `COMMIT` / `ROLLBACK` with MVCC isolation and full row-snapshot restore |
| **Joins** | `INNER`, `LEFT`, `RIGHT` across any two tables in one query |
| **Aggregation** | `GROUP BY` with `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` and `HAVING` |
| **Subqueries** | `col IN (GET table WHERE ...)` — correlated subquery in WHERE clause |

### Search

| | |
| --- | --- |
| **Vector search** | Cosine similarity via live HNSW index — O(log n) ANN, auto-indexed on `PUT` |
| **Fuzzy text** | Trigram similarity (`~`) — finds typos and partial matches |
| **AI search** | Hash-based lexical search across any text column |

### Operations

| | |
| --- | --- |
| **TLS** | Encrypted transport — `--tls-cert` / `--tls-key`; REPL `--tls` flag |
| **WAL encryption** | AES-256-GCM at-rest encryption via `PULSEDB_WAL_KEY` env var |
| **Auth** | Argon2id passwords, per-user RBAC, auth required by default |
| **Rate limiting** | 100 req/s per IP on REST endpoints; HTTP 429 on exceed |

### Ecosystem

| | |
| --- | --- |
| **Client SDKs** | Python, Go, and JavaScript / TypeScript clients — with native TLS support |
| **Connection pool** | Thread-safe pool for all three SDKs — `pip install pulsedb` / `npm i @pulsedb/client` |
| **ORM** | Django-style Python ORM · Sequelize-style JS ORM · Struct-tag Go ORM |
| **Migration** | One-command import from PostgreSQL, MySQL, SQLite, MongoDB |
| **REST API** | Auto-generated HTTP endpoints per table with per-table API key auth |

### Deployment

| | |
| --- | --- |
| **Windows** | MSI installer + Windows Service (`install` / `start` / `stop`) |
| **Linux** | Systemd unit via `systemd-unit` subcommand |
| **Docker** | Multi-stage image — single node or 3-node Raft cluster |
| **Clustering** | Raft consensus + write replication, peer heartbeats, FNV-1a shard routing, DDL replication |
| **LSM compaction** | Continuous background compaction every 30 s — log never grows unbounded |

---

## Quick Start

### Install

#### Windows

Download `pulsedb-1.0.0-x86_64.msi` from the [releases page](https://github.com/Ajaikumar0712/PulseDB/releases).

#### Docker

```bash
cp .env.example .env          # set PULSEDB_ADMIN_PASSWORD
docker compose up -d
docker exec -it pulsedb pulsedb-repl
```

#### Build from source

Requires [Rust 1.85+](https://rustup.rs)

```bash
git clone https://github.com/Ajaikumar0712/PulseDB
cd PulseDB && cargo build --release
```

### Run

```powershell
# Start the server (password printed once on first run — save it)
$env:PULSEDB_ADMIN_PASSWORD = "your-password"
pulsedb-server

# Connect
pulsedb-repl
```

```sql
AUTH admin 'your-password'

MAKE TABLE users (id int PRIMARY KEY, name text, age int)
PUT users { id: 1, name: "Alice", age: 30 }
PUT users { id: 2, name: "Bob",   age: 25 }

GET users WHERE age >= 28 ORDER BY name ASC
```

---

## Table of Contents

1. [Installation](#installation)
2. [Running the Server](#running-the-server)
3. [Windows Service](#windows-service)
4. [Linux Service (systemd)](#linux-service-systemd)
5. [TLS — Encrypted Transport](#tls--encrypted-transport)
6. [WAL Encryption — Data at Rest](#wal-encryption--data-at-rest)
7. [Connecting with the REPL](#connecting-with-the-repl)
8. [Client SDKs](#client-sdks)
9. [ORM](#orm)
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
2. Run the installer and follow the wizard
3. Keep "Add to PATH" checked
4. Click **Install**

Installs `pulsedb-server.exe` and `pulsedb-repl.exe` to `C:\Program Files\PulseDB\bin\` and adds them to `PATH`.

To uninstall: **Settings → Apps → PulseDB → Uninstall**

---

### Option B — Build from Source

**Requirements:** Rust 1.85+ — install from [rustup.rs](https://rustup.rs)

```bash
git clone https://github.com/Ajaikumar0712/PulseDB
cd PulseDB

cargo build --release    # optimised binary
cargo test               # 104 tests
```

| Binary | Path |
| --- | --- |
| `pulsedb-server` | `target/release/pulsedb-server` |
| `pulsedb-repl` | `target/release/pulsedb-repl` |

---

### Option C — Docker

```bash
# 1. Configure
cp .env.example .env
# Edit .env → set PULSEDB_ADMIN_PASSWORD=your-strong-password

# 2. Start
docker compose up -d

# 3. Connect
docker exec -it pulsedb pulsedb-repl
```

Port 7878 is bound to the **internal Docker network only** — not exposed to the host. For development access, add `ports: ["127.0.0.1:7878:7878"]` to `docker-compose.yml`.

Data persists in the named Docker volume `pulsedb-data`. A commented 3-node Raft cluster config is included in `docker-compose.yml`.

```bash
# Build and run manually
docker build -t pulsedb/pulsedb .
docker run -p 127.0.0.1:7878:7878 -v pulsedb-data:/var/lib/pulsedb pulsedb/pulsedb
```

---

## Running the Server

```bash
pulsedb-server
```

Logs stream as JSON to stdout. Press `Ctrl+C` to stop.

On **first start**, a random admin password is generated and printed once:

```text
=============================================================
 PulseDB admin password (save this — shown only once):
   user:     admin
   password: xK7mQpRt9nVw3bYs2cZj
 Set PULSEDB_ADMIN_PASSWORD env var to skip this on restart.
=============================================================
```

### Server Flags

| Flag | Default | Description |
| --- | --- | --- |
| `--addr <HOST:PORT>` | `127.0.0.1:7878` | Address and port to listen on |
| `--wal <PATH>` | `pulsedb.wal` | Path to the Write-Ahead Log file |
| `--data-dir <PATH>` | `pulsedb-data` | Catalog, snapshots, and config directory |
| `--log-level <LEVEL>` | `info` | `trace` · `debug` · `info` · `warn` · `error` |
| `--mode <MODE>` | `memory` | `memory` (default) or `disk` |
| `--row-cache <N>` | `500000` | Per-table in-memory row limit (disk mode) |
| `--admin-user <NAME>` | `admin` | Initial admin username |
| `--admin-password <PWD>` | *(auto-generated)* | Also read from `PULSEDB_ADMIN_PASSWORD` env var |
| `--no-auth` | *(off)* | Disable auth — dev and localhost only |
| `--tls-cert <PATH>` | *(off)* | PEM certificate (requires `--tls-key`) |
| `--tls-key <PATH>` | *(off)* | PEM private key |

```bash
# Production — fixed password via env var
$env:PULSEDB_ADMIN_PASSWORD = "strong-password"
pulsedb-server

# Development — no auth
pulsedb-server --no-auth

# TLS enabled
pulsedb-server --tls-cert cert.pem --tls-key key.pem

# Disk mode — datasets larger than RAM
pulsedb-server --mode disk --row-cache 200000

# Listen on all interfaces
pulsedb-server --addr 0.0.0.0:7878
```

---

## Windows Service

Run PulseDB automatically at boot — no terminal required.

> All service commands require an **Administrator** terminal.

```powershell
pulsedb-server install      # register once
pulsedb-server start        # start
pulsedb-repl                # connect (no admin required)
pulsedb-server stop         # stop
pulsedb-server uninstall    # remove
```

Manage via Services: `Win+R → services.msc → "PulseDB Database"`

---

## Linux Service (systemd)

```bash
# Generate and install the unit file
pulsedb-server --addr 0.0.0.0:7878 --data-dir /var/lib/pulsedb systemd-unit \
  | sudo tee /etc/systemd/system/pulsedb.service

sudo systemctl daemon-reload
sudo systemctl enable --now pulsedb
```

The generated unit runs as a `pulsedb` system user with `Restart=on-failure`.

---

## TLS — Encrypted Transport

Required for any network that isn't localhost.

```bash
# Generate a self-signed cert (development)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=localhost"

# Start with TLS
pulsedb-server --tls-cert cert.pem --tls-key key.pem
```

```bash
pulsedb-repl --tls --tls-no-verify          # self-signed (dev)
pulsedb-repl --tls --addr server.example.com:7878   # CA-signed (prod)
```

| Flag | Description |
| --- | --- |
| `--tls` | Enable TLS on the REPL connection |
| `--tls-no-verify` | Skip certificate verification — self-signed certs only |

---

## WAL Encryption — Data at Rest

Encrypt WAL files and snapshots with AES-256-GCM:

```bash
# Generate a 32-byte key (64 hex chars)
export PULSEDB_WAL_KEY="$(openssl rand -hex 32)"
$env:PULSEDB_WAL_KEY = (openssl rand -hex 32)   # PowerShell

pulsedb-server
```

- The key is never stored by PulseDB — supply it on every start
- Store it in a secrets manager: AWS Secrets Manager, HashiCorp Vault, Azure Key Vault
- Existing unencrypted WAL records are read transparently; new records are encrypted
- Losing the key means losing access to encrypted WAL data

---

## Connecting with the REPL

```bash
pulsedb-repl                               # localhost
pulsedb-repl --addr 192.168.1.10:7878     # remote
pulsedb-repl --tls --addr server:7878     # remote with TLS
```

| Input | Action |
| --- | --- |
| Any PulseQL statement | Execute and print result |
| `help` or `\help` | Show command reference |
| `exit`, `quit`, `\q` | Disconnect |
| `Ctrl+C` / `Ctrl+D` | Disconnect |

---

## Client SDKs

Official clients in `clients/` — each wraps the TCP + JSON protocol with zero extra dependencies.

> Connect over localhost or a trusted private network. For remote connections, route through a TLS-terminating reverse proxy (nginx, Caddy).

### Python

```bash
pip install pulsedb                    # PyPI
# or directly from the repo:
pip install ./clients/python
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

**Connection pooling** — for multi-threaded servers:

```python
from pulsedb.pool import ConnectionPool

pool = ConnectionPool(host="127.0.0.1", port=7878, min_size=2, max_size=10,
                      username=os.environ["PULSEDB_USER"],
                      password=os.environ["PULSEDB_PASSWORD"])

with pool.acquire() as db:
    result = db.query("GET users LIMIT 10")
```

---

### Go

```bash
go get github.com/Ajaikumar0712/PulseDB/clients/go
```

```go
c, _ := pulsedb.Connect("127.0.0.1:7878")
defer c.Close()
c.Auth(os.Getenv("PULSEDB_USER"), os.Getenv("PULSEDB_PASSWORD"))
result, _ := c.Query("GET users WHERE age >= 28")
for _, row := range result.Rows {
    fmt.Println(row.Get("id"), row.Get("name"))
}
```

`Client` is thread-safe — share across goroutines.

### JavaScript / TypeScript

```bash
# Copy clients/javascript/index.js into your project
```

```js
const { PulseDB } = require('./clients/javascript');

const db = await PulseDB.connect({ host: '127.0.0.1', port: 7878 });
await db.auth(process.env.PULSEDB_USER, process.env.PULSEDB_PASSWORD);
const result = await db.query('GET users WHERE age >= 28');
for (const row of result) { console.log(row.id, row.name); }
db.close();
```

TypeScript definitions available at `clients/javascript/orm.d.ts`.

---

## ORM

Declarative model layers — no raw PulseQL strings required. Full working examples in `clients/*/example_orm.*`.

### Python ORM

```python
from pulsedb.orm import connect, Model, IntField, TextField, FloatField, BoolField

db = connect("127.0.0.1", 7878)

class User(Model):
    class Meta:
        db    = db
        table = "users"
    id     = IntField(primary_key=True)
    name   = TextField()
    age    = IntField()
    active = BoolField(default=True)

User.create_table()

User.create(id=1, name="Alice", age=30)

# QuerySet API — chainable, lazy
adults = User.filter(age__gte=18, active=True).order_by("-age").limit(10).all()
alice  = User.get(id=1)
ids    = User.filter(id__in=[1, 2, 3]).all()

User.filter(id=2).update(active=False)
User.filter(active=False).delete()

# Vector + fuzzy search
User.similar("embedding", [0.9, 0.1, 0.2], k=10)
User.fuzzy("name", "alic", limit=5)

# Transactions
with db.transaction():
    User.create(id=2, name="Bob",   age=25)
    User.create(id=3, name="Carol", age=35)
```

**Lookup suffixes:** `__gt` `__gte` `__lt` `__lte` `__ne` `__in`

**Field types:** `IntField` · `FloatField` · `TextField` · `BoolField` · `VectorField` · `JsonField`

### JavaScript ORM

```js
const { defineModel, DataTypes, withTransaction } = require('./clients/javascript/orm');

const User = defineModel('users', {
  id:     { type: DataTypes.INT,  primaryKey: true },
  name:   { type: DataTypes.TEXT },
  age:    { type: DataTypes.INT },
  active: { type: DataTypes.BOOL, defaultValue: true },
}, { db });

await User.createTable();
await User.create({ id: 1, name: 'Alice', age: 30 });

const adults = await User.findAll({ where: { age: { gte: 18 } }, orderBy: '-age' });
const alice  = await User.findByPk(1);

await User.update({ active: false }, { where: { age: { lt: 18 } } });
await User.destroy({ where: { id: 99 } });

await User.similar('embedding', [0.9, 0.1, 0.2], { limit: 10 });
await User.fuzzy('name', 'alic', { limit: 5 });

await withTransaction(db, async () => {
  await User.bulkCreate([{ id: 2, name: 'Bob' }, { id: 3, name: 'Carol' }]);
});
```

### Go ORM

```go
type User struct {
    ID     int     `pulsedb:"id,primary_key"`
    Name   string  `pulsedb:"name"`
    Age    int     `pulsedb:"age"`
    Active bool    `pulsedb:"active"`
}

orm := pulsedb.NewORM(client)
orm.CreateTable(&User{})
orm.Create(&User{ID: 1, Name: "Alice", Age: 30, Active: true})

var users []User
orm.Q(&User{}).Where("age >= 18").OrderBy("age", false).Limit(10).Find(&users)

var alice User
orm.FindByPK(&alice, 1)

orm.Q(&User{}).Where("id = 1").Update(map[string]interface{}{"active": false})

orm.Transaction(func(o *pulsedb.ORM) error {
    return o.Create(&User{ID: 2, Name: "Bob", Age: 25})
})
```

No code generation — struct tags only.

---

## Migration Tool

One command to import from an existing database.

**Supported sources:** PostgreSQL · MySQL · SQLite · MongoDB

```bash
# Install the driver for your source (SQLite needs nothing)
pip install psycopg2-binary   # PostgreSQL
pip install pymysql           # MySQL
pip install pymongo           # MongoDB
```

```bash
# Dry run — preview schema, no writes
python tools/migrate.py postgres://postgres:pass@localhost/mydb --dry-run

# Migrate everything
python tools/migrate.py postgres://postgres:pass@localhost/mydb

# Specific tables, custom batch size
python tools/migrate.py sqlite:///myapp.db --tables users orders --batch 5000

# Remote PulseDB target
python tools/migrate.py mysql://root:pass@localhost/shop --target 10.0.0.5:7878
```

Automatically maps SQL types to PulseQL, copies rows in transaction batches, creates indexes, and shows live progress:

```text
  users  (100,000 rows)
  [████████████████████████████████████████] 100,000/100,000  100.0%
  ✓ 100,000 rows  4.2s  23,809 rows/s

═══════════════════════════════════════════════
  Migration complete
  Tables : 3      Rows : 542,000
  Time   : 18.4s  Rate : 29,456 rows/s
═══════════════════════════════════════════════
```

| Flag | Description |
| --- | --- |
| `--target HOST:PORT` | PulseDB address (default: `127.0.0.1:7878`) |
| `--tables T1 T2 …` | Migrate only specific tables |
| `--batch N` | Rows per transaction (default: 1000) |
| `--dry-run` | Preview without writing |
| `--skip-existing` | Skip tables already present in PulseDB |
| `--no-auth` | Connect without authenticating |

---

## PulseQL Language Reference

PulseQL is **not SQL** — different keywords, designed to be concise and unambiguous.

```text
Rules:
  • Statements separated by ;
  • Identifiers are case-insensitive
  • Strings use double quotes:  "hello"
  • Passwords use single quotes: 'secret'
```

---

### Data Types

| Type | Description | Example |
| --- | --- | --- |
| `int` | 64-bit signed integer | `42`, `-7` |
| `float` | 64-bit floating point | `3.14` |
| `text` | UTF-8 string | `"hello"` |
| `bool` | Boolean | `true`, `false` |
| `json` | Any JSON value | `{"k": "v"}` |
| `blob` | Raw bytes | — |
| `vector` | Dense f32 array for similarity search | `[0.1, 0.9, 0.3]` |
| `null` | Absent value | `null` |

---

### Table Management

```sql
MAKE TABLE users (id int PRIMARY KEY, name text, age int, active bool)
MAKE INDEX ON users (age)      -- speeds up WHERE and ORDER BY on `age`
DROP TABLE users
SHOW TABLES
```

---

### Writing Data

```sql
-- PUT — insert or replace a row (upsert on primary key)
PUT users { id: 1, name: "Alice", age: 30, active: true }

-- SET — update specific fields on matching rows
SET users { age: 31 } WHERE id = 1
SET users { active: false } WHERE age < 18
SET products { price: 0.0 }       -- no WHERE → update all rows

-- DEL — delete rows
DEL users WHERE id = 2
DEL users WHERE active = false
DEL users                          -- no WHERE → clear the table
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

**TIMEOUT** accepts `"500ms"` · `"5s"` · `"2m"` — must be a quoted string.

---

### Joins

```sql
GET users INNER JOIN orders ON users.id = orders.user_id
GET users LEFT  JOIN orders ON users.id = orders.user_id
GET users INNER JOIN orders ON users.id = orders.user_id
    WHERE orders.total > 20.0
    ORDER BY orders.total DESC LIMIT 5
```

| Type | Returns |
| --- | --- |
| `INNER JOIN` | Only rows matching in **both** tables |
| `LEFT JOIN` | All left rows; `null` for unmatched right columns |
| `RIGHT JOIN` | All right rows; `null` for unmatched left columns |

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

Trigram-based text similarity — handles typos and partial matches. Results ranked by score.

```sql
FIND users WHERE name ~ "alic"          -- finds "Alice"
FIND products WHERE name ~ "widge" LIMIT 5
GET users WHERE name ~ "alice"          -- ~ works inside GET too
```

---

### Vector Similarity Search

Store dense vectors and search for nearest neighbours by cosine similarity.

```sql
MAKE TABLE docs (id int PRIMARY KEY, text text, embedding vector)

PUT docs { id: 1, text: "Rust is fast",  embedding: [0.9, 0.1, 0.2] }
PUT docs { id: 2, text: "Python is easy", embedding: [0.2, 0.8, 0.4] }

SIMILAR docs ON embedding TO [0.88, 0.12, 0.22] LIMIT 10
```

Results include an `_score` column (0.0–1.0, higher is closer). Backed by a live **HNSW index** — vectors are indexed automatically on every `PUT`, removed on `DEL` and `DROP TABLE`. Search is O(log n) approximate nearest-neighbour.

`ON <column>` can be omitted when the table has exactly one `vector` column.

---

### Streaming Watch Queries

Server-push subscriptions — no polling required.

```sql
WATCH users WHERE active = true     -- opens a subscription
UNWATCH 1                           -- cancel by ID
```

The server streams one JSON line per change:

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

COMMIT      -- apply atomically
ROLLBACK    -- restore to state at BEGIN
```

`ROLLBACK` takes a row-level snapshot at `BEGIN` and restores it exactly — inserted rows are removed, updated rows reverted, deleted rows restored.

PulseDB uses **MVCC** — readers never block writers.

---

### REST API

Expose any table as HTTP endpoints.

```sql
API GENERATE FOR users    -- starts HTTP server, prints port + API key
API STOP FOR users
SHOW APIS
```

```text
REST API for `users` running at http://127.0.0.1:7879/api/users
API key: a3f9c2b1d4e8... (include as: Authorization: Bearer a3f9c2b1d4e8...)
```

| Method | Path | Action |
| --- | --- | --- |
| `GET` | `/api/<table>` | List all rows |
| `POST` | `/api/<table>` | Insert a row |
| `GET` | `/api/<table>/<id>` | Get one row |
| `PUT` | `/api/<table>/<id>` | Update fields |
| `DELETE` | `/api/<table>/<id>` | Delete a row |

All requests require `Authorization: Bearer <api_key>`.

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid API key |
| `413` | Request body exceeds 1 MiB |
| `429` | Rate limit exceeded (100 req/s per IP) |

```powershell
$h = @{ Authorization = "Bearer a3f9c2b1d4e8..." }
Invoke-RestMethod -Uri "http://127.0.0.1:7879/api/users" -Headers $h
```

---

### Security & User Management

Authentication is **on by default**. Passwords are hashed with **Argon2id** — memory-hard and GPU-resistant.

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

**Operations:** `select` · `insert` · `update` · `delete` · `all`

**Row-level security** — scope a grant to specific rows using `WITH FILTER`:

```sql
-- bob can only read his own rows
GRANT select ON orders TO bob WITH FILTER "owner_id = 42"

-- analysts see only recent events
GRANT select ON events TO analyst WITH FILTER "ts >= '2024-01-01'"
```

The filter is a PulseQL WHERE expression applied automatically on every `GET` — users cannot bypass it.

Pass `--no-auth` to disable authentication — **localhost / development only**.

---

### Cluster Commands

`MAKE TABLE` and `DROP TABLE` are automatically forwarded to all reachable peers after executing locally.

```sql
CLUSTER JOIN "192.168.1.20:7878"
CLUSTER PART "192.168.1.20:7878"
CLUSTER STATUS

CLUSTER SHARD ASSIGN orders SHARDS 4 NODES "n1:7878", "n2:7878", "n3:7878"
CLUSTER SHARD STATUS
CLUSTER SHARD DROP orders
```

---

### Triggers

> Requires an **admin session**. Trigger recursion is capped at depth 5.

```sql
TRIGGER audit_insert WHEN PUT  users  DO PUT audit { action: "user_added" }
TRIGGER bust_cache   WHEN SET  orders DO DEL order_cache
TRIGGER on_delete    WHEN DEL  users  DO PUT audit { action: "user_removed" }

DROP TRIGGER audit_insert
SHOW TRIGGERS
```

---

### Graph Queries

Traverse node–edge–node relationships across three tables.

```sql
GRAPH MATCH (a:people) -[rel:follows]-> (b:people)
    WHERE a.name = "Alice"
    LIMIT 10
```

The edge table must have `from_id` and `to_id` columns. Default `LIMIT` is 100; maximum is 10,000. `SELECT` permission is checked on all three tables.

**Multi-hop traversal** — add `*min..max` inside the edge brackets for BFS across N hops:

```sql
-- Everyone Alice can reach in 1 to 3 hops through 'follows'
GRAPH MATCH (a:people) -[rel:follows*1..3]-> (b:people)
    WHERE a.name = "Alice"
    LIMIT 50
```

Results include a `depth` column showing the hop count.

---

### Time Travel Queries

Retrieve exact historical snapshots. Requires `SELECT` permission.

`AS OF` replays committed WAL statements up to the given timestamp into a temporary in-memory database — you get the exact row state at that moment, not just a timestamp filter on live rows.

```sql
GET orders AS OF "2024-06-01T00:00:00Z" WHERE country = "US"
GET users VERSION 42
```

---

### Subqueries

Use `IN (GET ...)` to filter rows against the result of another query.

```sql
-- Users who have placed at least one order
GET users WHERE id IN (GET orders WHERE total > 0)

-- Products ordered by premium users
GET products WHERE id IN (
    GET order_items WHERE user_id IN (GET users WHERE plan = "premium")
)
```

The subquery is executed first, its first column collected as a value set, then the outer query filters using that set. Subqueries are nestable.

---

### AI Search

Hash-based lexical search — matches on token overlap, not synonyms.

```sql
AI SEARCH products "wireless headphones" LIMIT 5
```

> For semantic search, store model-generated embeddings in a `vector` column and use `SIMILAR`.

---

### Admin Commands

```sql
EXPLAIN GET users WHERE age > 25 ORDER BY age DESC LIMIT 10
CHECKPOINT             -- flush snapshot to disk, trim WAL
SHOW RUNNING QUERIES
KILL QUERY 42
METRICS
```

---

### Resource Configuration

Changes are persisted to `<data-dir>/pulsedb.config` and restored on the next start.

```sql
CONFIG SET max_connections    200
CONFIG SET max_rows_per_query 50000
CONFIG SET default_timeout_ms 10000
CONFIG SET max_memory_mb      4096

SHOW CONFIG
```

---

## Expressions & Operators

| Category | Operators |
| --- | --- |
| Comparison | `=` `!=` `<>` `<` `<=` `>` `>=` `~` |
| Logical | `AND` `OR` `NOT` |
| Arithmetic | `+` `-` `*` `/` |

```sql
GET users WHERE age >= 18 AND age <= 65 AND active = true
GET users WHERE name = "Alice" OR name = "Bob"
GET products WHERE price * 1.2 < 30.0
```

---

## Response Format

All communication uses **newline-terminated JSON**.

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

```json
{"status":"ok","result":{"Ok":{"message":"1 row inserted","elapsed_ms":0}}}
```

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
Uptime               : 5m 32s
Queries total        : 47
Queries errored      : 1
Rows inserted        : 20
Rows updated         : 3
Rows deleted         : 2
Transactions committed  : 4
Transactions rolled back: 0
WAL records written  : 35

Latency (ms)
  avg  : 1.20
  min  : 0
  p50  : 1
  p95  : 4
  p99  : 9
  max  : 12
```

---

## Benchmarks

### Internal — Rust Criterion

```bash
cargo bench                           # all 12 groups
cargo bench -- vector_search          # one group
cargo bench -- --save-baseline main   # save baseline
cargo bench -- --baseline main        # compare
# Report: target/criterion/report/index.html
```

| Group | Scales |
| --- | --- |
| `insert` | 1K → 1M rows |
| `point_lookup` | 10K / 100K / 1M dataset |
| `range_scan` | 10% of dataset |
| `full_scan` | Filter, no index |
| `aggregation` | GROUP BY + COUNT + AVG |
| `order_limit` | ORDER BY + LIMIT 100 |
| `fuzzy_search` | Trigram `~` |
| `vector_search` | HNSW 128-dim cosine, k=10 |
| `transaction` | 1 / 10 / 50 / 100 ops/tx |
| `parser` | 8 query types |
| `mixed_80r_20w` | 80% reads / 20% writes |
| `delete` | DEL WHERE 50% |

### Comparison — Python Suite

Compares against PostgreSQL, MongoDB, Redis, and Qdrant.

```bash
pip install psycopg2-binary pymongo redis qdrant-client
cd benchmarks

python run_all.py --skip-errors                             # 100K rows, 100 concurrent
python run_all.py --rows 1000000 --concurrency 1000         # 1M rows, 1000 concurrent
python run_all.py --dbs pulsedb qdrant --vec-rows 100000    # vector search only
```

Measures INSERT · POINT LOOKUP · RANGE SCAN · FULL SCAN · AGGREGATION · ORDER BY LIMIT · FUZZY SEARCH · VECTOR SEARCH · CONCURRENT TPS with p50 / p95 / p99 latency. Results saved to `benchmarks/results/comparison.json`.

---

## Architecture

```mermaid
flowchart TD
    subgraph CLIENTS["CLIENTS"]
        direction LR
        A["pulsedb-repl"] ~~~ B["Python / Go / JS SDKs"]
        B ~~~ C["REST API clients"]
        C ~~~ D["Migration Tool"]
    end

    CLIENTS -->|"TCP · TLS"| SERVER

    subgraph SERVER["PulseDB SERVER"]
        direction TB
        subgraph QE["QUERY ENGINE"]
            P[Parser] --> PL[Planner] --> EX[Executor]
        end
        subgraph TL["TRANSACTION LAYER  —  MVCC + WAL + ROLLBACK"]
        end
        subgraph SE["STORAGE ENGINE"]
            MEM[In-Memory Tables] --- IDX["B-tree · HNSW · Trigram Indexes"]
        end
        subgraph BG["BACKGROUND SERVICES"]
            direction LR
            WAL[WAL Writer] ~~~ SNAP[Snapshot]
            SNAP ~~~ WATCH[Watch Engine]
            WATCH ~~~ RAFT[Raft Cluster]
        end
        QE --> TL --> SE
        SE -.-> BG
    end
```

### How crash recovery works

1. On restart, catalog + row snapshots load from `--data-dir`
2. Committed WAL statements since the last `CHECKPOINT` are replayed in transaction order
3. Rolled-back transactions are automatically skipped

`CHECKPOINT` flushes a full snapshot to disk and trims the WAL.

### Layer Breakdown

| Layer | Components | Responsibility |
| --- | --- | --- |
| **Query Engine** | `lexer` → `parser` → `planner` → `executor` | Parse PulseQL, build AST, cost-estimate, execute |
| **Transaction Layer** | `transaction.rs`, `mvcc.rs`, `wal.rs` | MVCC, BEGIN/COMMIT/ROLLBACK with row-snapshot restore, WAL statement logging |
| **Storage Engine** | `table.rs`, `columnar.rs`, `lsm.rs`, HNSW | In-memory B-tree tables, live HNSW indexes, columnar compression |
| **Background Services** | `wal.rs`, `persist.rs`, `watch.rs`, `cluster/` | WAL flush, snapshots, streaming push, Raft consensus, MVCC vacuum |

### Repository

```text
src/              Rust server
clients/          Python · Go · JavaScript SDKs + ORMs
tools/            migrate.py (database import tool)
benchmarks/       Comparison suite vs PostgreSQL, MongoDB, Redis, Qdrant
benches/          Criterion internal benchmarks (12 groups)
wix/              Windows MSI installer definition
```

---

## Quick Reference Card

```sql
-- ── Schema ────────────────────────────────────────────────────────────
MAKE TABLE t (id int PRIMARY KEY, name text, val float, emb vector)
MAKE INDEX ON t (val)
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
TRIGGER on_insert WHEN PUT t DO PUT audit { action: "insert" }
DROP TRIGGER on_insert
SHOW TRIGGERS

-- ── Graph ─────────────────────────────────────────────────────────────
GRAPH MATCH (a:people) -[rel:follows]-> (b:people) WHERE a.name = "Alice" LIMIT 10
GRAPH MATCH (a:people) -[rel:follows*1..3]-> (b:people) WHERE a.name = "Alice" LIMIT 50

-- ── Subqueries ────────────────────────────────────────────────────────
GET users WHERE id IN (GET orders WHERE total > 100)

-- ── REST API ──────────────────────────────────────────────────────────
API GENERATE FOR t
API STOP FOR t
SHOW APIS

-- ── Security ──────────────────────────────────────────────────────────
AUTH alice 'secret'
CREATE USER bob PASSWORD 'pass'
CREATE ADMIN USER carol PASSWORD 'admin'
GRANT select ON t TO bob
GRANT all ON * TO carol
REVOKE insert ON t FROM bob
DROP USER bob
SHOW USERS

-- ── Cluster ───────────────────────────────────────────────────────────
CLUSTER JOIN "192.168.1.20:7878"
CLUSTER PART "192.168.1.20:7878"
CLUSTER STATUS
CLUSTER SHARD ASSIGN orders SHARDS 4 NODES "n1:7878", "n2:7878"
CLUSTER SHARD STATUS
CLUSTER SHARD DROP orders

-- ── Admin ─────────────────────────────────────────────────────────────
CONFIG SET max_connections 200
CONFIG SET default_timeout_ms 10000
SHOW CONFIG
EXPLAIN GET t WHERE val > 5
CHECKPOINT
METRICS
SHOW RUNNING QUERIES
KILL QUERY 7
```

---

## Troubleshooting

### Port already in use

```bash
pulsedb-server --addr 127.0.0.1:7879
```

### "connection refused" — REPL can't connect

The server isn't running. Start it: `pulsedb-server --no-auth`

### "not authenticated"

```sql
AUTH admin 'your-password'
```

Lost the password? Set a new one and restart:

```bash
$env:PULSEDB_ADMIN_PASSWORD = "new-password"
pulsedb-server
```

### "table does not exist"

```sql
MAKE TABLE users (id int PRIMARY KEY, name text)
```

### PUT silently replaced a row

`PUT` is upsert by design. Use `SET` to update specific fields without replacing the whole row.

### Query timeout

```sql
GET large_table LIMIT 1000 TIMEOUT "30s"
CONFIG SET default_timeout_ms 30000
```

### "GRAPH MATCH LIMIT cannot exceed 10000"

Reduce the `LIMIT` on your query. The cap prevents memory exhaustion on dense graphs.

### WAL growing large

```sql
CHECKPOINT
```

Flushes a full snapshot to disk and trims the WAL.

### Windows Service — "access denied"

Run PowerShell as Administrator.

### TLS — server refuses plain connection

With `--tls-cert` / `--tls-key` set, the server only accepts TLS:

```bash
pulsedb-repl --tls --tls-no-verify   # self-signed
pulsedb-repl --tls                    # CA-signed
```

### Migration — driver not found

```bash
pip install psycopg2-binary   # PostgreSQL
pip install pymysql           # MySQL
pip install pymongo           # MongoDB
```

---

## License & Pricing

PulseDB is released under the **Business Source License 1.1 (BUSL-1.1)**.

| Use case | |
| --- | :-: |
| Personal use, learning, experimentation | ✅ Free |
| Open-source non-commercial projects | ✅ Free |
| 30-day production trial | ✅ Free |
| Commercial production use (after trial) | Contact us |

On **March 11, 2030**, PulseDB automatically converts to the **Apache License 2.0** and becomes free for all uses, forever.

### Commercial Plans

| Plan | Price | Includes |
| --- | --- | --- |
| **Starter** | $500 / year | 1 server · 1 company |
| **Business** | $2,000 / year | Up to 5 servers |
| **Enterprise** | Contact us | Unlimited · priority support · SLA |

### Contact

- Email: [pulsedb.license@gmail.com](mailto:pulsedb.license@gmail.com)
- Web: <https://fornexus.tech>

---

*Built by [Ajaikumar](https://fornexus.tech) · [ForNexus](https://fornexus.tech)*
