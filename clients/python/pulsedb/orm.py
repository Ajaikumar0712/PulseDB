"""
PulseDB Python ORM.

Provides a declarative, Django-inspired model layer on top of the raw
PulseDB TCP client.  No extra dependencies — uses only the stdlib and the
existing `pulsedb` client package.

Example
-------
::

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

    # Schema
    User.create_table()          # MAKE TABLE users (...)

    # Write
    alice = User.create(id=1, name="Alice", age=30)
    User.create(id=2, name="Bob", age=25, active=False)

    # Query
    users = User.all()
    user  = User.get(id=1)
    adults = User.filter(age__gte=18).order_by("age").limit(10).all()

    # Update
    User.filter(id=1).update(age=31)

    # Delete
    User.filter(active=False).delete()

    # Transactions
    with db.transaction():
        User.create(id=3, name="Carol", age=35)
        User.filter(id=2).update(active=True)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Generic, Iterator, List, Optional, Type, TypeVar

from . import PulseDB, PulseDBError

# ── Type variable for model subclasses ───────────────────────────────────────

M = TypeVar("M", bound="Model")

# ── Connection factory ────────────────────────────────────────────────────────

def connect(
    host: str = "127.0.0.1",
    port: int = 7878,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> "Connection":
    """
    Open a PulseDB connection for use with the ORM.

    ::

        db = connect("127.0.0.1", 7878,
                     username=os.environ["PULSEDB_USER"],
                     password=os.environ["PULSEDB_PASSWORD"])
    """
    return Connection(host, port, username, password)


class Connection:
    """Wraps PulseDBClient with ORM-friendly helpers."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7878,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._client = PulseDB.connect(host, port)
        if username and password:
            self._client.auth(username, password)
        self._in_tx = False

    def execute(self, q: str) -> Any:
        return self._client.query(q)

    def transaction(self) -> "_Transaction":
        return _Transaction(self)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class _Transaction:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def __enter__(self) -> Connection:
        self._conn.execute("BEGIN")
        return self._conn

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type:
            self._conn.execute("ROLLBACK")
        else:
            self._conn.execute("COMMIT")


# ── Field descriptors ─────────────────────────────────────────────────────────

class Field:
    """Base field descriptor."""

    pulseql_type: str = "any"

    def __init__(
        self,
        *,
        primary_key: bool = False,
        nullable: bool = True,
        default: Any = None,
    ) -> None:
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.name: str = ""          # set by ModelMeta

    def contribute_to_class(self, name: str) -> None:
        self.name = name

    def to_pulseql(self, value: Any) -> str:
        """Serialize `value` to a PulseQL literal."""
        if value is None:
            return "null"
        return str(value)

    def from_row(self, value: Any) -> Any:
        """Deserialize a value from a PulseDB result row."""
        return value

    def schema_fragment(self) -> str:
        flags = ""
        if self.primary_key:
            flags = " PRIMARY KEY"
        return f"{self.name} {self.pulseql_type}{flags}"


class IntField(Field):
    pulseql_type = "int"

    def to_pulseql(self, value: Any) -> str:
        if value is None:
            return "null"
        return str(int(value))

    def from_row(self, value: Any) -> Optional[int]:
        return int(value) if value is not None else None


class FloatField(Field):
    pulseql_type = "float"

    def to_pulseql(self, value: Any) -> str:
        if value is None:
            return "null"
        return str(float(value))

    def from_row(self, value: Any) -> Optional[float]:
        return float(value) if value is not None else None


class TextField(Field):
    pulseql_type = "text"

    def to_pulseql(self, value: Any) -> str:
        if value is None:
            return "null"
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def from_row(self, value: Any) -> Optional[str]:
        return str(value) if value is not None else None


class BoolField(Field):
    pulseql_type = "bool"

    def to_pulseql(self, value: Any) -> str:
        if value is None:
            return "null"
        return "true" if value else "false"

    def from_row(self, value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")


class VectorField(Field):
    pulseql_type = "vector"

    def to_pulseql(self, value: Any) -> str:
        if value is None:
            return "null"
        nums = ", ".join(str(float(v)) for v in value)
        return f"[{nums}]"

    def from_row(self, value: Any) -> Optional[List[float]]:
        if value is None:
            return None
        if isinstance(value, list):
            return [float(v) for v in value]
        return None


class JsonField(Field):
    pulseql_type = "json"

    def to_pulseql(self, value: Any) -> str:
        import json as _json
        if value is None:
            return "null"
        return _json.dumps(value)

    def from_row(self, value: Any) -> Any:
        return value


# ── Model metaclass ───────────────────────────────────────────────────────────

class ModelMeta(type):
    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> "ModelMeta":
        fields: Dict[str, Field] = {}

        # Inherit fields from base classes
        for base in bases:
            if hasattr(base, "_fields"):
                fields.update(base._fields)

        # Collect Field instances declared in this class
        for attr, value in list(namespace.items()):
            if isinstance(value, Field):
                value.contribute_to_class(attr)
                fields[attr] = value

        namespace["_fields"] = fields
        cls = super().__new__(mcs, name, bases, namespace)
        return cls


# ── QuerySet ──────────────────────────────────────────────────────────────────

class QuerySet(Generic[M]):
    """
    Lazy query builder — evaluation happens on iteration or explicit calls.

    Supports:
      .filter(**kwargs)     → add WHERE conditions (AND)
      .exclude(**kwargs)    → negate conditions (NOT)
      .order_by(*cols)      → ORDER BY col [DESC if prefixed with '-']
      .limit(n)             → LIMIT n
      .all()                → evaluate, return list of model instances
      .first()              → evaluate, return first instance or None
      .count()              → evaluate, return integer
      .update(**kwargs)     → SET ... WHERE ..., return affected count
      .delete()             → DEL ... WHERE ..., return affected count
      .similar(col, vector) → SIMILAR ... LIMIT n  (vector search)
      .fuzzy(col, pattern)  → FIND ... WHERE col ~ "pattern"

    Lookup suffixes on filter():
      field=value           → field = value
      field__gt=value       → field > value
      field__gte=value      → field >= value
      field__lt=value       → field < value
      field__lte=value      → field <= value
      field__ne=value       → field != value
      field__in=[a,b,c]     → (field = a OR field = b OR field = c)
    """

    def __init__(self, model_cls: Type[M]) -> None:
        self._model = model_cls
        self._wheres: List[str] = []
        self._order: List[str] = []
        self._limit_n: Optional[int] = None
        self._fuzzy_col: Optional[str] = None
        self._fuzzy_pat: Optional[str] = None
        self._similar_col: Optional[str] = None
        self._similar_vec: Optional[List[float]] = None
        self._similar_k: int = 10

    def _clone(self) -> "QuerySet[M]":
        q = QuerySet(self._model)
        q._wheres        = list(self._wheres)
        q._order         = list(self._order)
        q._limit_n       = self._limit_n
        q._fuzzy_col     = self._fuzzy_col
        q._fuzzy_pat     = self._fuzzy_pat
        q._similar_col   = self._similar_col
        q._similar_vec   = self._similar_vec
        q._similar_k     = self._similar_k
        return q

    # ── Chaining ─────────────────────────────────────────────────────────────

    def filter(self, **kwargs: Any) -> "QuerySet[M]":
        q = self._clone()
        for lookup, value in kwargs.items():
            q._wheres.append(self._parse_lookup(lookup, value, negate=False))
        return q

    def exclude(self, **kwargs: Any) -> "QuerySet[M]":
        q = self._clone()
        for lookup, value in kwargs.items():
            q._wheres.append(self._parse_lookup(lookup, value, negate=True))
        return q

    def order_by(self, *columns: str) -> "QuerySet[M]":
        q = self._clone()
        for col in columns:
            if col.startswith("-"):
                q._order.append(f"{col[1:]} DESC")
            else:
                q._order.append(f"{col} ASC")
        return q

    def limit(self, n: int) -> "QuerySet[M]":
        q = self._clone()
        q._limit_n = n
        return q

    def fuzzy(self, column: str, pattern: str, limit: int = 20) -> "QuerySet[M]":
        """Trigram similarity search."""
        q = self._clone()
        q._fuzzy_col = column
        q._fuzzy_pat = pattern
        q._limit_n = limit
        return q

    def similar(self, column: str, vector: List[float], k: int = 10) -> "QuerySet[M]":
        """Vector cosine similarity search."""
        q = self._clone()
        q._similar_col = column
        q._similar_vec = vector
        q._similar_k = k
        return q

    # ── Evaluation ────────────────────────────────────────────────────────────

    def all(self) -> List[M]:
        return list(self._execute_read())

    def first(self) -> Optional[M]:
        return next(iter(self.limit(1)._execute_read()), None)

    def count(self) -> int:
        return len(self.all())

    def exists(self) -> bool:
        return self.first() is not None

    def get(self, **kwargs: Any) -> M:
        if kwargs:
            qs = self.filter(**kwargs)
        else:
            qs = self
        results = qs.limit(2).all()
        if not results:
            raise self._model.DoesNotExist(
                f"{self._model.__name__} matching query does not exist"
            )
        if len(results) > 1:
            raise self._model.MultipleObjectsReturned(
                f"get() returned more than one {self._model.__name__}"
            )
        return results[0]

    def update(self, **kwargs: Any) -> int:
        fields = self._model._fields
        parts = []
        for k, v in kwargs.items():
            field = fields.get(k)
            lit = field.to_pulseql(v) if field else TextField().to_pulseql(v)
            parts.append(f"{k}: {lit}")
        set_clause = "{ " + ", ".join(parts) + " }"
        table = self._model.Meta.table
        where = self._build_where()
        q = f"SET {table} {set_clause}{where}"
        result = self._db().execute(q)
        return getattr(result, "affected", 0) or 0

    def delete(self) -> int:
        table = self._model.Meta.table
        where = self._build_where()
        q = f"DEL {table}{where}"
        result = self._db().execute(q)
        return getattr(result, "affected", 0) or 0

    def __iter__(self) -> Iterator[M]:
        return iter(self.all())

    def __len__(self) -> int:
        return self.count()

    # ── Query builders ────────────────────────────────────────────────────────

    def _build_where(self) -> str:
        if not self._wheres:
            return ""
        return " WHERE " + " AND ".join(self._wheres)

    def _build_order(self) -> str:
        if not self._order:
            return ""
        return " ORDER BY " + ", ".join(self._order)

    def _build_limit(self) -> str:
        if self._limit_n is None:
            return ""
        return f" LIMIT {self._limit_n}"

    def _build_get_stmt(self) -> str:
        table = self._model.Meta.table
        return (
            f"GET {table}"
            + self._build_where()
            + self._build_order()
            + self._build_limit()
        )

    def _build_find_stmt(self) -> str:
        table = self._model.Meta.table
        pat = self._fuzzy_pat.replace('"', '\\"')
        lim = f" LIMIT {self._limit_n}" if self._limit_n else ""
        return f'FIND {table} WHERE {self._fuzzy_col} ~ "{pat}"{lim}'

    def _build_similar_stmt(self) -> str:
        table = self._model.Meta.table
        vec = "[" + ", ".join(str(v) for v in self._similar_vec) + "]"
        col = f" ON {self._similar_col}" if self._similar_col else ""
        return f"SIMILAR {table}{col} TO {vec} LIMIT {self._similar_k}"

    def _execute_read(self) -> Iterator[M]:
        if self._similar_vec is not None:
            stmt = self._build_similar_stmt()
        elif self._fuzzy_col is not None:
            stmt = self._build_find_stmt()
        else:
            stmt = self._build_get_stmt()

        result = self._db().execute(stmt)
        cols = result.columns
        for raw_row in result.rows:
            yield self._model._from_row(cols, raw_row)

    def _db(self) -> Connection:
        return self._model.Meta.db

    # ── Lookup parser ─────────────────────────────────────────────────────────

    def _parse_lookup(self, lookup: str, value: Any, negate: bool) -> str:
        if "__" in lookup:
            col, suffix = lookup.rsplit("__", 1)
        else:
            col, suffix = lookup, "eq"

        field = self._model._fields.get(col)
        lit = field.to_pulseql(value) if field else TextField().to_pulseql(value)

        ops = {
            "eq":  f"{col} = {lit}",
            "ne":  f"{col} != {lit}",
            "gt":  f"{col} > {lit}",
            "gte": f"{col} >= {lit}",
            "lt":  f"{col} < {lit}",
            "lte": f"{col} <= {lit}",
        }

        if suffix == "in":
            if not value:
                return "1 = 0"  # always false
            literals = []
            for v in value:
                f2 = field.to_pulseql(v) if field else TextField().to_pulseql(v)
                literals.append(f"{col} = {f2}")
            expr = "(" + " OR ".join(literals) + ")"
        elif suffix in ops:
            expr = ops[suffix]
        else:
            raise ValueError(f"Unknown lookup suffix: '{suffix}'")

        return f"NOT ({expr})" if negate else expr


# ── Model base class ──────────────────────────────────────────────────────────

class Model(metaclass=ModelMeta):
    """
    Base class for PulseDB ORM models.

    Subclass and define fields + a ``Meta`` inner class::

        class Product(Model):
            class Meta:
                db    = my_connection
                table = "products"

            id    = IntField(primary_key=True)
            name  = TextField()
            price = FloatField()
            tags  = JsonField(nullable=True)

        Product.create_table()
        Product.create(id=1, name="Widget", price=9.99)
        cheap = Product.filter(price__lt=20.0).order_by("price").all()
    """

    _fields: Dict[str, Field] = {}

    class Meta:
        db:    Connection
        table: str = ""

    # ── Exceptions ────────────────────────────────────────────────────────────

    class DoesNotExist(Exception):
        pass

    class MultipleObjectsReturned(Exception):
        pass

    # ── Schema management ─────────────────────────────────────────────────────

    @classmethod
    def create_table(cls, if_not_exists: bool = True) -> None:
        """Run MAKE TABLE ... based on the declared fields."""
        cols = []
        for name, field in cls._fields.items():
            cols.append(field.schema_fragment())
        col_defs = ", ".join(cols)
        q = f"MAKE TABLE {cls.Meta.table} ({col_defs})"
        try:
            cls.Meta.db.execute(q)
        except PulseDBError as e:
            if if_not_exists and "already exists" in str(e).lower():
                return
            raise

    @classmethod
    def drop_table(cls) -> None:
        cls.Meta.db.execute(f"DROP TABLE {cls.Meta.table}")

    @classmethod
    def create_index(cls, *columns: str) -> None:
        for col in columns:
            cls.Meta.db.execute(f"MAKE INDEX ON {cls.Meta.table} ({col})")

    # ── Write operations ──────────────────────────────────────────────────────

    @classmethod
    def create(cls: Type[M], **kwargs: Any) -> M:
        """Insert a new row and return the model instance."""
        instance = cls(**kwargs)
        instance.save()
        return instance

    def save(self) -> None:
        """PUT (upsert) this instance into the database."""
        parts = []
        for name, field in self._fields.items():
            value = getattr(self, name, field.default)
            lit = field.to_pulseql(value)
            parts.append(f"{name}: {lit}")
        row_dict = "{ " + ", ".join(parts) + " }"
        self.Meta.db.execute(f"PUT {self.Meta.table} {row_dict}")

    def delete(self) -> None:
        """Delete this specific instance by primary key."""
        pk_field, pk_name = self._primary_key()
        pk_value = getattr(self, pk_name)
        lit = pk_field.to_pulseql(pk_value)
        self.Meta.db.execute(f"DEL {self.Meta.table} WHERE {pk_name} = {lit}")

    def update(self, **kwargs: Any) -> None:
        """Update specific fields on this instance and persist the change."""
        for k, v in kwargs.items():
            setattr(self, k, v)
        pk_field, pk_name = self._primary_key()
        pk_value = getattr(self, pk_name)
        pk_lit = pk_field.to_pulseql(pk_value)
        parts = []
        for k, v in kwargs.items():
            field = self._fields.get(k)
            lit = field.to_pulseql(v) if field else TextField().to_pulseql(v)
            parts.append(f"{k}: {lit}")
        set_clause = "{ " + ", ".join(parts) + " }"
        self.Meta.db.execute(
            f"SET {self.Meta.table} {set_clause} WHERE {pk_name} = {pk_lit}"
        )

    # ── Read / QuerySet ───────────────────────────────────────────────────────

    @classmethod
    def objects(cls: Type[M]) -> QuerySet[M]:
        return QuerySet(cls)

    @classmethod
    def all(cls: Type[M]) -> List[M]:
        return QuerySet(cls).all()

    @classmethod
    def filter(cls: Type[M], **kwargs: Any) -> QuerySet[M]:
        return QuerySet(cls).filter(**kwargs)

    @classmethod
    def exclude(cls: Type[M], **kwargs: Any) -> QuerySet[M]:
        return QuerySet(cls).exclude(**kwargs)

    @classmethod
    def get(cls: Type[M], **kwargs: Any) -> M:
        return QuerySet(cls).get(**kwargs)

    @classmethod
    def first(cls: Type[M]) -> Optional[M]:
        return QuerySet(cls).first()

    @classmethod
    def count(cls) -> int:
        return QuerySet(cls).count()

    @classmethod
    def similar(cls: Type[M], column: str, vector: List[float], k: int = 10) -> List[M]:
        """Vector similarity search — returns k nearest neighbours."""
        return QuerySet(cls).similar(column, vector, k).all()

    @classmethod
    def fuzzy(cls: Type[M], column: str, pattern: str, limit: int = 20) -> List[M]:
        """Trigram text similarity search."""
        return QuerySet(cls).fuzzy(column, pattern, limit).all()

    # ── Internals ─────────────────────────────────────────────────────────────

    def __init__(self, **kwargs: Any) -> None:
        for name, field in self._fields.items():
            value = kwargs.get(name, field.default)
            object.__setattr__(self, name, value)
        # Allow extra kwargs (e.g. _score from SIMILAR results)
        for k, v in kwargs.items():
            if k not in self._fields:
                object.__setattr__(self, k, v)

    @classmethod
    def _from_row(cls: Type[M], columns: List[str], values: list) -> M:
        data = {}
        for col, val in zip(columns, values):
            field = cls._fields.get(col)
            data[col] = field.from_row(val) if field else val
        return cls(**data)

    @classmethod
    def _primary_key(cls) -> tuple[Field, str]:
        for name, field in cls._fields.items():
            if field.primary_key:
                return field, name
        raise AttributeError(f"{cls.__name__} has no primary key field")

    def __repr__(self) -> str:
        try:
            _, pk_name = self._primary_key()
            pk = getattr(self, pk_name, "?")
            return f"<{self.__class__.__name__} {pk_name}={pk}>"
        except AttributeError:
            return f"<{self.__class__.__name__}>"

    def to_dict(self) -> Dict[str, Any]:
        return {name: getattr(self, name, None) for name in self._fields}
