# pulsedb — Python client

```bash
pip install pulsedb
```

## Quick start

```python
from pulsedb import PulseDB

with PulseDB.connect(host="127.0.0.1", port=7878) as db:
    db.query('MAKE TABLE users (id int, name text, score float)')
    db.query('PUT users (1, "Alice", 9.5)')
    db.query('PUT users (2, "Bob",   7.2)')

    result = db.query('GET users ORDER BY score DESC')
    for row in result:
        print(row.id, row.name, row.score)

    # Transactions
    db.query('BEGIN')
    db.query('PUT users (3, "Carol", 8.1)')
    db.query('COMMIT')
```

## Authentication

```python
db = PulseDB.connect()
db.auth("admin", "s3cr3t")
```

## Error handling

```python
from pulsedb import PulseDB, PulseDBError

with PulseDB.connect() as db:
    try:
        result = db.query('GET nonexistent_table')
    except PulseDBError as e:
        print(f"Query failed: {e}")
```
