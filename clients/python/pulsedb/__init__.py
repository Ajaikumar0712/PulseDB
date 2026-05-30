"""PulseDB Python client — connects over TCP and speaks PulseQL."""

import json
import socket
from typing import Any, Dict, Iterator, List, Optional


class PulseDBError(Exception):
    pass


class Row:
    """A single result row with attribute-style access."""

    def __init__(self, columns: List[str], values: List[Any]) -> None:
        self._data = dict(zip(columns, values))

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"Row({self._data!r})"

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._data)


class Result:
    """Wraps a PulseDB query response."""

    def __init__(self, raw: Dict[str, Any]) -> None:
        self._raw = raw

    @property
    def ok(self) -> bool:
        return "error" not in self._raw

    @property
    def error(self) -> Optional[str]:
        return self._raw.get("error")

    @property
    def message(self) -> Optional[str]:
        return self._raw.get("message")

    @property
    def affected(self) -> Optional[int]:
        return self._raw.get("affected")

    @property
    def elapsed_ms(self) -> Optional[int]:
        return self._raw.get("elapsed_ms")

    @property
    def columns(self) -> List[str]:
        return self._raw.get("columns", [])

    @property
    def rows(self) -> List[Row]:
        cols = self.columns
        return [Row(cols, r) for r in self._raw.get("rows", [])]

    def __iter__(self) -> Iterator[Row]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self._raw.get("rows", []))

    def __repr__(self) -> str:
        if not self.ok:
            return f"Result(error={self.error!r})"
        if self.columns:
            return f"Result({len(self)} rows, columns={self.columns})"
        return f"Result(ok, message={self.message!r})"


class PulseDB:
    """
    Synchronous PulseDB client.

    Usage::

        with PulseDB.connect() as db:
            db.query('MAKE TABLE users (id int, name text)')
            db.query('PUT users (1, "Alice")')
            result = db.query('GET users')
            for row in result:
                print(row.id, row.name)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7878) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._file = None

    @classmethod
    def connect(cls, host: str = "127.0.0.1", port: int = 7878) -> "PulseDB":
        client = cls(host, port)
        client._connect()
        return client

    def _connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((self.host, self.port))
        self._file = self._sock.makefile("r", encoding="utf-8")

    def auth(self, username: str, password: str) -> "PulseDB":
        """Authenticate immediately after connecting."""
        result = self.query(f"AUTH '{username}' '{password}'")
        if not result.ok:
            raise PulseDBError(f"auth failed: {result.error}")
        return self

    def query(self, q: str) -> Result:
        if self._sock is None:
            raise PulseDBError("not connected — call connect() first")
        payload = json.dumps({"query": q}) + "\n"
        self._sock.sendall(payload.encode("utf-8"))
        line = self._file.readline()
        if not line:
            raise PulseDBError("connection closed by server")
        raw = json.loads(line)
        result = Result(raw)
        if not result.ok:
            raise PulseDBError(result.error or "unknown error")
        return result

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._file = None

    def __enter__(self) -> "PulseDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "connected" if self._sock else "closed"
        return f"PulseDB({self.host}:{self.port}, {status})"
