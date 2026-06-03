"""
PulseDB connection pool for Python.

Thread-safe pool of PulseDB connections — ideal for multi-threaded web
applications (Flask, FastAPI, Django) where many threads share a database.

Usage::

    from pulsedb.pool import ConnectionPool

    pool = ConnectionPool(
        host="127.0.0.1", port=7878,
        min_size=2, max_size=10,
        username=os.environ["PULSEDB_USER"],
        password=os.environ["PULSEDB_PASSWORD"],
    )

    with pool.acquire() as db:
        result = db.query("GET users LIMIT 10")
        for row in result:
            print(row.id, row.name)
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from . import PulseDB, PulseDBError


class ConnectionPool:
    """
    A thread-safe pool of PulseDB connections.

    Parameters
    ----------
    host        Server address (default: 127.0.0.1)
    port        Server port   (default: 7878)
    min_size    Connections to open eagerly at construction time
    max_size    Hard cap on total open connections
    username    Authenticated user (omit for --no-auth servers)
    password    Password for that user
    tls         Enable TLS transport
    tls_verify  Verify TLS certificate (False = allow self-signed)
    timeout     Seconds to wait for a free connection before raising
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7878,
        min_size: int = 1,
        max_size: int = 10,
        username: Optional[str] = None,
        password: Optional[str] = None,
        tls: bool = False,
        tls_verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._tls = tls
        self._tls_verify = tls_verify
        self._timeout = timeout
        self._max_size = max_size

        self._lock = threading.Lock()
        self._pool: list[PulseDB] = []
        self._in_use: set[int] = set()  # id() of checked-out connections
        self._sem = threading.Semaphore(max_size)

        # Open min_size connections eagerly
        for _ in range(min(min_size, max_size)):
            self._pool.append(self._make_conn())

    # ── Public API ────────────────────────────────────────────────────────

    @contextmanager
    def acquire(self) -> Iterator[PulseDB]:
        """
        Check out a connection from the pool.

        Blocks up to `timeout` seconds if no connection is available.
        Returns the connection to the pool on exit, even if an exception
        was raised.

        ::

            with pool.acquire() as db:
                db.query("GET users")
        """
        conn = self._checkout()
        try:
            yield conn
        finally:
            self._checkin(conn)

    def close_all(self) -> None:
        """Close every connection and empty the pool."""
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()
            self._in_use.clear()

    @property
    def size(self) -> int:
        """Total connections (idle + in-use)."""
        with self._lock:
            return len(self._pool) + len(self._in_use)

    @property
    def idle(self) -> int:
        """Number of connections currently available."""
        with self._lock:
            return len(self._pool)

    # ── Internal ──────────────────────────────────────────────────────────

    def _make_conn(self) -> PulseDB:
        conn = PulseDB.connect(
            self._host, self._port,
            tls=self._tls,
            tls_verify=self._tls_verify,
        )
        if self._username and self._password:
            conn.auth(self._username, self._password)
        return conn

    def _checkout(self) -> PulseDB:
        if not self._sem.acquire(timeout=self._timeout):
            raise PulseDBError(
                f"connection pool exhausted (max={self._max_size}) — "
                f"increase max_size or timeout"
            )
        with self._lock:
            if self._pool:
                conn = self._pool.pop()
            else:
                conn = self._make_conn()
            self._in_use.add(id(conn))
        return conn

    def _checkin(self, conn: PulseDB) -> None:
        with self._lock:
            self._in_use.discard(id(conn))
            # Verify the connection is still alive before returning it
            try:
                conn.query("SHOW TABLES")
                self._pool.append(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                # Replace with a fresh connection
                try:
                    fresh = self._make_conn()
                    self._pool.append(fresh)
                except Exception:
                    pass
        self._sem.release()

    def __enter__(self) -> "ConnectionPool":
        return self

    def __exit__(self, *_) -> None:
        self.close_all()
