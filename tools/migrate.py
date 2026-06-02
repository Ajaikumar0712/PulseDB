#!/usr/bin/env python3
"""
PulseDB Migration Tool — import tables, rows, and indexes from:
  PostgreSQL  postgres://user:pass@host:5432/dbname
  MySQL       mysql://user:pass@host:3306/dbname
  SQLite      sqlite:///path/to/file.db
  MongoDB     mongodb://host:27017/dbname

Usage:
  python migrate.py postgres://postgres:pass@localhost/mydb
  python migrate.py postgres://... --target 127.0.0.1:7878
  python migrate.py postgres://... --tables users orders products
  python migrate.py postgres://... --batch 5000 --dry-run
  python migrate.py postgres://... --no-auth

Requirements (install only for the source you need):
  pip install psycopg2-binary     # PostgreSQL
  pip install pymysql             # MySQL
  pip install pymongo             # MongoDB
  # SQLite is stdlib — no install needed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import urlparse


# ── ANSI colours ─────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def red(t):    return _c("31", t)
def cyan(t):   return _c("36", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)


# ── Progress bar ──────────────────────────────────────────────────────────────

def progress(current: int, total: int, label: str = "", width: int = 40) -> str:
    pct  = current / max(total, 1)
    done = int(pct * width)
    bar  = "█" * done + "░" * (width - done)
    return f"\r  [{bar}] {current:>8,}/{total:<8,}  {pct:5.1%}  {label:<20}"


# ── PulseDB client (raw TCP) ──────────────────────────────────────────────────

class PulseDBClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7878):
        self._sock = socket.create_connection((host, port), timeout=30)
        self._file = self._sock.makefile("r", encoding="utf-8")
        line = self._file.readline()  # welcome banner

    def query(self, q: str) -> dict:
        payload = json.dumps({"query": q}) + "\n"
        self._sock.sendall(payload.encode())
        line = self._file.readline()
        if not line:
            raise ConnectionError("Server closed connection")
        resp = json.loads(line)
        if resp.get("status") == "error":
            raise RuntimeError(resp.get("message", "unknown error"))
        return resp

    def auth(self, user: str, password: str) -> None:
        self.query(f"AUTH {user} '{password}'")

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


# ── Type mapping ──────────────────────────────────────────────────────────────

# Maps source SQL types → PulseQL types
_PG_TYPE_MAP = {
    "integer": "int", "bigint": "int", "smallint": "int", "int": "int",
    "int2": "int", "int4": "int", "int8": "int", "serial": "int",
    "bigserial": "int", "numeric": "float", "decimal": "float",
    "real": "float", "double precision": "float", "float4": "float",
    "float8": "float", "boolean": "bool", "bool": "bool",
    "text": "text", "varchar": "text", "character varying": "text",
    "char": "text", "bpchar": "text", "uuid": "text", "name": "text",
    "json": "json", "jsonb": "json", "bytea": "blob",
    "timestamp": "text", "timestamptz": "text", "date": "text",
    "time": "text", "timetz": "text", "interval": "text",
    "array": "json",
}

_MYSQL_TYPE_MAP = {
    "int": "int", "bigint": "int", "smallint": "int", "tinyint": "int",
    "mediumint": "int", "float": "float", "double": "float", "decimal": "float",
    "boolean": "bool", "bool": "bool", "tinyint(1)": "bool",
    "varchar": "text", "char": "text", "text": "text", "longtext": "text",
    "mediumtext": "text", "tinytext": "text", "json": "json",
    "blob": "blob", "longblob": "blob", "mediumblob": "blob",
    "datetime": "text", "timestamp": "text", "date": "text", "time": "text",
}


def pg_type_to_pulseql(pg_type: str) -> str:
    base = re.sub(r"\(.*\)", "", pg_type.lower()).strip()
    return _PG_TYPE_MAP.get(base, "text")


def mysql_type_to_pulseql(mysql_type: str) -> str:
    base = mysql_type.lower().split("(")[0].strip()
    return _MYSQL_TYPE_MAP.get(base, "text")


# ── Source connectors ─────────────────────────────────────────────────────────

@dataclass
class ColumnInfo:
    name:        str
    type:        str          # PulseQL type
    primary_key: bool = False
    nullable:    bool = True


@dataclass
class TableInfo:
    name:    str
    columns: List[ColumnInfo]
    indexes: List[List[str]] = field(default_factory=list)
    row_count: int = 0


class SourceConnector:
    def connect(self): ...
    def list_tables(self, include: Optional[List[str]] = None) -> List[str]: ...
    def describe_table(self, table: str) -> TableInfo: ...
    def iter_rows(self, table: str, batch: int) -> Generator[List[Any], None, None]: ...
    def close(self): ...


class PostgreSQLSource(SourceConnector):
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn = None

    def connect(self):
        try:
            import psycopg2
        except ImportError:
            sys.exit(red("  psycopg2 not found. Run: pip install psycopg2-binary"))
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = True

    def list_tables(self, include=None) -> List[str]:
        with self._conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            tables = [r[0] for r in cur.fetchall()]
        if include:
            tables = [t for t in tables if t in include]
        return tables

    def describe_table(self, table: str) -> TableInfo:
        with self._conn.cursor() as cur:
            # Columns
            cur.execute("""
                SELECT c.column_name, c.data_type, c.is_nullable,
                       CASE WHEN kcu.column_name IS NOT NULL THEN true ELSE false END AS is_pk
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage kcu
                    ON c.table_name = kcu.table_name
                    AND c.column_name = kcu.column_name
                    AND kcu.constraint_name IN (
                        SELECT constraint_name FROM information_schema.table_constraints
                        WHERE table_name = %s AND constraint_type = 'PRIMARY KEY'
                    )
                WHERE c.table_name = %s AND c.table_schema = 'public'
                ORDER BY c.ordinal_position
            """, (table, table))
            cols = []
            for name, dtype, nullable, is_pk in cur.fetchall():
                cols.append(ColumnInfo(
                    name=name,
                    type=pg_type_to_pulseql(dtype),
                    primary_key=bool(is_pk),
                    nullable=nullable == "YES",
                ))

            # Indexes
            cur.execute("""
                SELECT a.attname
                FROM pg_class t, pg_class i, pg_index ix, pg_attribute a
                WHERE t.oid = ix.indrelid AND i.oid = ix.indexrelid
                    AND a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
                    AND t.relkind = 'r' AND NOT ix.indisprimary
                    AND t.relname = %s
            """, (table,))
            idx_cols = [r[0] for r in cur.fetchall()]

            # Row count
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = cur.fetchone()[0]

        return TableInfo(name=table, columns=cols, indexes=[[c] for c in idx_cols],
                         row_count=count)

    def iter_rows(self, table: str, batch: int) -> Generator:
        with self._conn.cursor() as cur:
            cur.execute(f'SELECT * FROM "{table}"')
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                col_names = [desc[0] for desc in cur.description]
                yield col_names, rows

    def close(self):
        if self._conn:
            self._conn.close()


class SQLiteSource(SourceConnector):
    def __init__(self, path: str):
        self._path = path
        self._conn = None

    def connect(self):
        import sqlite3
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row

    def list_tables(self, include=None) -> List[str]:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
        if include:
            tables = [t for t in tables if t in include]
        return tables

    def describe_table(self, table: str) -> TableInfo:
        cur = self._conn.execute(f"PRAGMA table_info('{table}')")
        cols = []
        for row in cur.fetchall():
            _, name, dtype, notnull, _, pk = row
            pulseql_type = pg_type_to_pulseql(dtype or "text")
            cols.append(ColumnInfo(
                name=name, type=pulseql_type,
                primary_key=bool(pk), nullable=not notnull,
            ))
        count = self._conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
        return TableInfo(name=table, columns=cols, row_count=count)

    def iter_rows(self, table: str, batch: int) -> Generator:
        cur = self._conn.execute(f"SELECT * FROM '{table}'")
        col_names = [d[0] for d in cur.description]
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            yield col_names, [tuple(r) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()


class MySQLSource(SourceConnector):
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn = None

    def connect(self):
        try:
            import pymysql
        except ImportError:
            sys.exit(red("  pymysql not found. Run: pip install pymysql"))
        p = urlparse(self._dsn)
        self._conn = pymysql.connect(
            host=p.hostname, port=p.port or 3306,
            user=p.username, password=p.password,
            db=p.path.lstrip("/"), charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    def list_tables(self, include=None) -> List[str]:
        with self._conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [list(r.values())[0] for r in cur.fetchall()]
        if include:
            tables = [t for t in tables if t in include]
        return tables

    def describe_table(self, table: str) -> TableInfo:
        with self._conn.cursor() as cur:
            cur.execute(f"DESCRIBE `{table}`")
            cols = []
            for row in cur.fetchall():
                name = row["Field"]
                dtype = row["Type"].lower().split("(")[0]
                is_pk = row["Key"] == "PRI"
                nullable = row["Null"] == "YES"
                cols.append(ColumnInfo(
                    name=name, type=mysql_type_to_pulseql(dtype),
                    primary_key=is_pk, nullable=nullable,
                ))
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            count = cur.fetchone()["n"]
        return TableInfo(name=table, columns=cols, row_count=count)

    def iter_rows(self, table: str, batch: int) -> Generator:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{table}`")
            col_names = [d[0] for d in cur.description]
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                yield col_names, [tuple(r.values()) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()


class MongoDBSource(SourceConnector):
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._db = None
        self._client = None

    def connect(self):
        try:
            from pymongo import MongoClient
        except ImportError:
            sys.exit(red("  pymongo not found. Run: pip install pymongo"))
        p = urlparse(self._dsn)
        self._client = MongoClient(self._dsn, serverSelectionTimeoutMS=5000)
        self._db = self._client[p.path.lstrip("/") or "default"]

    def list_tables(self, include=None) -> List[str]:
        tables = self._db.list_collection_names()
        if include:
            tables = [t for t in tables if t in include]
        return tables

    def describe_table(self, table: str) -> TableInfo:
        col = self._db[table]
        sample = col.find_one()
        cols = [ColumnInfo(name="_id", type="text", primary_key=True)]
        if sample:
            for k, v in sample.items():
                if k == "_id":
                    continue
                if isinstance(v, bool):
                    t = "bool"
                elif isinstance(v, int):
                    t = "int"
                elif isinstance(v, float):
                    t = "float"
                elif isinstance(v, (dict, list)):
                    t = "json"
                else:
                    t = "text"
                cols.append(ColumnInfo(name=k, type=t))
        return TableInfo(name=table, columns=cols, row_count=col.estimated_document_count())

    def iter_rows(self, table: str, batch: int) -> Generator:
        col = self._db[table]
        col_names = None
        buf = []
        for doc in col.find():
            doc["_id"] = str(doc["_id"])
            if col_names is None:
                col_names = list(doc.keys())
            buf.append(tuple(doc.get(c) for c in col_names))
            if len(buf) >= batch:
                yield col_names, buf
                buf = []
        if buf:
            yield col_names, buf

    def close(self):
        if self._client:
            self._client.close()


def make_source(dsn: str) -> SourceConnector:
    scheme = dsn.split("://")[0].lower()
    if scheme in ("postgres", "postgresql"):
        return PostgreSQLSource(dsn)
    elif scheme == "mysql":
        return MySQLSource(dsn)
    elif scheme == "sqlite":
        path = dsn.replace("sqlite:///", "").replace("sqlite://", "")
        return SQLiteSource(path)
    elif scheme in ("mongodb", "mongo"):
        return MongoDBSource(dsn)
    else:
        sys.exit(red(f"  Unsupported source: {scheme}. Use postgres://, mysql://, sqlite:///, or mongodb://"))


# ── Row serialiser ────────────────────────────────────────────────────────────

def value_to_pulseql(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, bytes):
        return f'"<blob:{len(value)}B>"'
    # str, datetime, uuid, etc.
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def row_to_put(table: str, col_names: List[str], row: tuple) -> str:
    parts = ", ".join(
        f"{col}: {value_to_pulseql(val)}"
        for col, val in zip(col_names, row)
    )
    return f"PUT {table} {{ {parts} }}"


# ── Migration engine ──────────────────────────────────────────────────────────

@dataclass
class MigrationResult:
    table:   str
    rows:    int = 0
    skipped: int = 0
    errors:  int = 0
    elapsed: float = 0.0


def migrate_table(
    source: SourceConnector,
    dest:   PulseDBClient,
    info:   TableInfo,
    batch:  int,
    dry_run: bool,
    skip_existing: bool,
) -> MigrationResult:
    result = MigrationResult(table=info.name)
    t0 = time.perf_counter()

    # Build MAKE TABLE statement
    col_defs = []
    for c in info.columns:
        pk = " PRIMARY KEY" if c.primary_key else ""
        col_defs.append(f"{c.name} {c.type}{pk}")
    make_stmt = f"MAKE TABLE {info.name} ({', '.join(col_defs)})"

    if dry_run:
        print(f"    [DRY RUN] {make_stmt}")
    else:
        try:
            dest.query(make_stmt)
        except RuntimeError as e:
            if "already exists" in str(e).lower():
                if skip_existing:
                    print(yellow(f"    Table '{info.name}' already exists — skipping"))
                    return result
            else:
                raise

    # Copy rows
    total = info.row_count
    done = 0

    for col_names, rows in source.iter_rows(info.name, batch):
        if not dry_run:
            dest.query("BEGIN")
        for row in rows:
            stmt = row_to_put(info.name, col_names, row)
            if dry_run:
                if done == 0:
                    print(dim(f"    [DRY RUN] {stmt[:120]}..."))
            else:
                try:
                    dest.query(stmt)
                    result.rows += 1
                except RuntimeError:
                    result.errors += 1
            done += 1
        if not dry_run:
            dest.query("COMMIT")
        print(progress(done, total, info.name), end="", flush=True)

    print()  # newline after progress bar

    # Create indexes
    for idx_cols in info.indexes:
        for col in idx_cols:
            idx_stmt = f"MAKE INDEX ON {info.name} ({col})"
            if dry_run:
                print(dim(f"    [DRY RUN] {idx_stmt}"))
            else:
                try:
                    dest.query(idx_stmt)
                except RuntimeError:
                    pass  # index may already exist

    result.elapsed = time.perf_counter() - t0
    if not dry_run:
        result.rows = done - result.errors
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="Migrate tables from PostgreSQL/MySQL/SQLite/MongoDB into PulseDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python migrate.py postgres://postgres:pass@localhost/mydb
  python migrate.py postgres://... --target 192.168.1.10:7878
  python migrate.py sqlite:///myapp.db --tables users orders
  python migrate.py mysql://root:pass@localhost/shop --batch 5000
  python migrate.py mongodb://localhost/analytics --dry-run
  python migrate.py postgres://... --pulsedb-user admin --pulsedb-password secret
        """,
    )
    parser.add_argument("source_dsn", help="Source database DSN")
    parser.add_argument("--target",   default="127.0.0.1:7878",
                        help="PulseDB address (default: 127.0.0.1:7878)")
    parser.add_argument("--tables",   nargs="+", metavar="TABLE",
                        help="Only migrate specific tables")
    parser.add_argument("--batch",    type=int, default=1000,
                        help="Rows per transaction batch (default: 1000)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print SQL without writing to PulseDB")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip tables that already exist in PulseDB")
    parser.add_argument("--pulsedb-user",     default=None)
    parser.add_argument("--pulsedb-password", default=None)
    parser.add_argument("--no-auth",  action="store_true",
                        help="Connect to PulseDB without authenticating")
    args = parser.parse_args()

    host_port = args.target.rsplit(":", 1)
    host = host_port[0]
    port = int(host_port[1]) if len(host_port) > 1 else 7878

    print(bold("\n  PulseDB Migration Tool"))
    print(f"  Source : {cyan(args.source_dsn)}")
    print(f"  Target : {cyan(args.target)}")
    if args.dry_run:
        print(yellow("  Mode   : DRY RUN (no data will be written)"))
    print()

    # ── Connect to source ─────────────────────────────────────────────────────
    print("Connecting to source...", end=" ", flush=True)
    source = make_source(args.source_dsn)
    try:
        source.connect()
    except Exception as e:
        print(red(f"FAILED\n  {e}"))
        sys.exit(1)
    print(green("OK"))

    # ── Connect to PulseDB ────────────────────────────────────────────────────
    print("Connecting to PulseDB...", end=" ", flush=True)
    try:
        dest = PulseDBClient(host, port)
    except Exception as e:
        print(red(f"FAILED\n  {e}\n  Is the server running? pulsedb-server --no-auth"))
        sys.exit(1)

    if not args.no_auth:
        user = args.pulsedb_user or os.environ.get("PULSEDB_USER", "admin")
        pw   = args.pulsedb_password or os.environ.get("PULSEDB_PASSWORD", "")
        if pw:
            try:
                dest.auth(user, pw)
            except Exception as e:
                print(red(f"Auth failed: {e}"))
                sys.exit(1)
    print(green("OK"))

    # ── Discover tables ───────────────────────────────────────────────────────
    print("\nDiscovering tables...", end=" ", flush=True)
    try:
        tables = source.list_tables(args.tables)
    except Exception as e:
        print(red(f"FAILED\n  {e}"))
        sys.exit(1)
    print(green(f"{len(tables)} table(s) found"))

    if not tables:
        print(yellow("  No tables to migrate."))
        sys.exit(0)

    # ── Introspect each table ─────────────────────────────────────────────────
    print("\nInspecting schema...")
    table_infos: List[TableInfo] = []
    total_rows = 0
    for tname in tables:
        try:
            info = source.describe_table(tname)
            table_infos.append(info)
            total_rows += info.row_count
            print(f"  {tname:<30} {info.row_count:>10,} rows  "
                  f"{len(info.columns)} cols  "
                  f"{len(info.indexes)} indexes")
        except Exception as e:
            print(red(f"  {tname}: {e}"))

    print(f"\n  Total: {bold(str(len(table_infos)))} tables, "
          f"{bold(f'{total_rows:,}')} rows\n")

    if not args.dry_run:
        inp = input("Proceed? [Y/n] ").strip().lower()
        if inp not in ("", "y", "yes"):
            print("Aborted.")
            sys.exit(0)

    # ── Migrate ───────────────────────────────────────────────────────────────
    wall_start = time.perf_counter()
    results: List[MigrationResult] = []

    for info in table_infos:
        print(f"\n{bold(info.name)}  ({info.row_count:,} rows)")
        try:
            r = migrate_table(source, dest, info, args.batch, args.dry_run, args.skip_existing)
            results.append(r)
            rate = r.rows / r.elapsed if r.elapsed > 0 else 0
            status = green("✓") if r.errors == 0 else yellow("⚠")
            print(f"  {status} {r.rows:,} rows  {r.elapsed:.1f}s  {rate:,.0f} rows/s"
                  + (f"  {red(str(r.errors)+' errors')}" if r.errors else ""))
        except Exception as e:
            print(red(f"  ✗ ERROR: {e}"))
            results.append(MigrationResult(table=info.name, errors=1))

    # ── Summary ───────────────────────────────────────────────────────────────
    wall_elapsed = time.perf_counter() - wall_start
    total_migrated = sum(r.rows for r in results)
    total_errors   = sum(r.errors for r in results)

    print(f"\n{'═' * 60}")
    print(bold("  Migration complete"))
    print(f"  Tables   : {len(results)}")
    print(f"  Rows     : {green(f'{total_migrated:,}')}")
    if total_errors:
        print(f"  Errors   : {red(str(total_errors))}")
    print(f"  Time     : {wall_elapsed:.1f}s")
    print(f"  Rate     : {total_migrated / max(wall_elapsed, 0.001):,.0f} rows/s")
    print(f"{'═' * 60}\n")

    if args.dry_run:
        print(yellow("  Dry run complete. No data was written."))
        print(yellow("  Remove --dry-run to perform the actual migration.\n"))

    source.close()
    dest.close()


if __name__ == "__main__":
    main()
