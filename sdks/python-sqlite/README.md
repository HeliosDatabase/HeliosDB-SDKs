# heliosdb-sqlite

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/HeliosDatabase/HeliosDB-SDKs/blob/main/LICENSE)

**SQLite-compatible interface for HeliosDB** - A drop-in replacement for Python's `sqlite3` module with enhanced features including vector search, encryption, and time-travel queries.

---

## Features

- **sqlite3 DB-API interface** - Drop-in replacement for Python's `sqlite3` module, including `cursor.lastrowid`
- **Vector Search** - Built-in vector search with Product Quantization (8-16x compression)
- **Transparent Encryption** - AES-256-GCM encryption with <3% overhead
- **Time-Travel Queries** - Access historical data with `AS OF TIMESTAMP`
- **Database Branching** - Git-like workflows for schema changes
- **PostgreSQL Types** - Extended type support (JSONB, UUID, VECTOR)
- **Typed results, in process** - Embedded mode runs the HeliosDB Nano engine inside your Python process (`heliosdb-nano-embedded`) and returns `int`, `float`, `str`, `bytes` and `None` exactly as `sqlite3` does
- **Daemon mode** - The same API against a HeliosDB Nano server over the PostgreSQL wire protocol (needs `psycopg2`)

---

## Installation

`heliosdb-sqlite` is not yet published on PyPI; install it from the
[HeliosDB-SDKs](https://github.com/HeliosDatabase/HeliosDB-SDKs) repository.

### Requirement: the HeliosDB Nano engine

Embedded mode (the default) runs the engine **inside the Python process**
through the `heliosdb-nano-embedded` package (import name `heliosdb_nano`), a
PyO3 binding of HeliosDB Nano. It is installed automatically on Linux x86_64,
the platform it publishes wheels for; elsewhere install it when a wheel for
your platform is available:

```bash
pip install heliosdb-nano-embedded
```

Without it, embedded mode falls back to driving a `heliosdb-nano repl`
subprocess and emits a `RuntimeWarning`, because that transport reads a
printed text table and returns **every value as `str`** (`'7'` instead of
`7`). For the fallback, download `heliosdb-nano` from the
[HeliosDB-Nano releases](https://github.com/HeliosDatabase/HeliosDB-Nano/releases)
and put it on your `PATH`, or point `HELIOSDB_BINARY` at it (lookup order:
`$HELIOSDB_BINARY`, `heliosdb-nano` on `PATH`, `heliosdb` on `PATH`, then a
binary bundled under `heliosdb_sqlite/binaries/`).

Choose the embedded transport explicitly with
`connect(..., embedded_backend='binding' | 'repl' | 'auto')` or the
`HELIOSDB_SQLITE_BACKEND` environment variable. `'binding'` raises
`InterfaceError` instead of falling back.

### Standard Installation

```bash
pip install "heliosdb-sqlite @ git+https://github.com/HeliosDatabase/HeliosDB-SDKs.git#subdirectory=sdks/python-sqlite"
```

### With Optional Dependencies

```bash
# Vector operations (numpy, scipy)
pip install "heliosdb-sqlite[vector] @ git+https://github.com/HeliosDatabase/HeliosDB-SDKs.git#subdirectory=sdks/python-sqlite"

# Pandas integration
pip install "heliosdb-sqlite[pandas] @ git+https://github.com/HeliosDatabase/HeliosDB-SDKs.git#subdirectory=sdks/python-sqlite"

# All features
pip install "heliosdb-sqlite[all] @ git+https://github.com/HeliosDatabase/HeliosDB-SDKs.git#subdirectory=sdks/python-sqlite"
```

### Verify Installation

```bash
python -c "import heliosdb_sqlite; print(heliosdb_sqlite.__version__)"
# Output: 3.1.0

# Run comprehensive tests
python -m heliosdb_sqlite.cli check
```

---

## Quick Start

### Drop-in Replacement for sqlite3

```python
# Change this:
# import sqlite3
# To this:
import heliosdb_sqlite as sqlite3

# Everything else works exactly the same!
conn = sqlite3.connect(':memory:')
cursor = conn.cursor()

cursor.execute('CREATE TABLE users (id INTEGER, name TEXT)')
cursor.execute('INSERT INTO users VALUES (?, ?)', (1, 'Alice'))
conn.commit()

cursor.execute('SELECT * FROM users')
print(cursor.fetchall())  # [(1, 'Alice')] in embedded and daemon mode;
                          # see "Result types"

conn.close()
```

### Vector Search Example

```python
import heliosdb_sqlite as db

conn = db.connect('vectors.db')
cursor = conn.cursor()

# Create table with vector column
cursor.execute('''
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        title TEXT,
        embedding VECTOR(768)
    )
''')

# Insert document with vector
import numpy as np
embedding = np.random.rand(768).tolist()
cursor.execute(
    'INSERT INTO documents VALUES (?, ?, ?)',
    (1, 'HeliosDB Guide', embedding)
)
conn.commit()

# Semantic search (k-NN)
query_vector = np.random.rand(768).tolist()
cursor.execute('''
    SELECT id, title, embedding <=> ? AS distance
    FROM documents
    ORDER BY distance
    LIMIT 5
''', (query_vector,))

for row in cursor.fetchall():
    print(f"Document {row[0]}: {row[1]} (distance: {row[2]:.4f})")

conn.close()
```

### Time-Travel Queries

```python
import heliosdb_sqlite as db

conn = db.connect('audit.db')
cursor = conn.cursor()

# Query historical data
cursor.execute('''
    SELECT * FROM orders
    AS OF TIMESTAMP '2025-01-01 12:00:00'
    WHERE customer_id = ?
''', (123,))

historical_orders = cursor.fetchall()
print(f"Orders as of 2025-01-01: {historical_orders}")

conn.close()
```

### Encrypted Database

```python
import heliosdb_sqlite as db
import os

# Set encryption key
os.environ['HELIOSDB_ENCRYPTION_KEY'] = 'your-32-byte-key-here'

# Database is automatically encrypted
conn = db.connect('encrypted.db')
cursor = conn.cursor()

cursor.execute('CREATE TABLE secrets (id INTEGER, data TEXT)')
cursor.execute('INSERT INTO secrets VALUES (?, ?)', (1, 'confidential'))
conn.commit()
conn.close()

# Data is encrypted at rest with AES-256-GCM
```

---

## API Reference

### Connection Methods

```python
import heliosdb_sqlite

# Create connection
conn = heliosdb_sqlite.connect(
    database='mydb.db',           # ':memory:' for in-memory
    timeout=5.0,                  # Lock timeout in seconds
    isolation_level='DEFERRED',   # Transaction isolation
    check_same_thread=True        # Thread safety check
)

# Execute queries
cursor = conn.cursor()
conn.commit()
conn.rollback()
conn.close()

# Context manager (auto-commit/rollback)
with heliosdb_sqlite.connect(':memory:') as conn:
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE test (id INTEGER)')
```

### Cursor Methods

```python
cursor = conn.cursor()

# Execute single statement
cursor.execute('SELECT * FROM users WHERE id = ?', (1,))

# Execute with named parameters
cursor.execute('SELECT * FROM users WHERE name = :name', {'name': 'Alice'})

# Execute many (batch insert)
cursor.executemany(
    'INSERT INTO users VALUES (?, ?)',
    [(1, 'Alice'), (2, 'Bob'), (3, 'Charlie')]
)

# Execute script (multiple statements)
cursor.executescript('''
    CREATE TABLE users (id INTEGER, name TEXT);
    CREATE INDEX idx_name ON users(name);
''')

# Fetch results
row = cursor.fetchone()           # Single row
rows = cursor.fetchmany(10)       # Multiple rows
all_rows = cursor.fetchall()      # All remaining rows

# Iterate over results
for row in cursor:
    print(row)
```

### `cursor.lastrowid`

After an `INSERT ... VALUES` into a table whose primary key is an integer
column (`INTEGER`, `BIGINT`, `SERIAL`, ...), `cursor.lastrowid` holds the key
of the inserted row, as in `sqlite3`. For a multi-row `INSERT` it is the key
of the last row. Tables without an integer primary key, and
`INSERT ... SELECT`, leave it `None`.

```python
cur.execute("CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT)")
cur.execute("INSERT INTO users (name) VALUES (?)", ("Alice",))
print(cur.lastrowid)  # 1
```

The layer appends `RETURNING <pk>` to the `INSERT` and hides that result
set. The primary-key column is looked up once per table and cached. To turn
the rewrite off, use `connect(..., lastrowid=False)`.

### Daemon mode (PostgreSQL wire protocol)

`mode='daemon'` connects to a running HeliosDB Nano server over the
PostgreSQL wire protocol instead of starting a local REPL. It needs
`psycopg2`:

```bash
pip install "heliosdb-sqlite[daemon] @ git+https://github.com/HeliosDatabase/HeliosDB-SDKs.git#subdirectory=sdks/python-sqlite"
```

```python
conn = heliosdb_sqlite.connect(
    'app',                     # ignored in daemon mode
    mode='daemon',
    dsn='postgresql://helios@db.example.com:5432/heliosdb?sslmode=require',
    server_password=os.environ['HELIOSDB_PASSWORD'],
)
```

Connection settings: `dsn` (a libpq connection string or `postgresql://`
URI), or `server_host` (default `127.0.0.1`), `server_port` (`5432`),
`server_user` (`helios`), `server_database` (`heliosdb`); `server_password`.
Keywords override the matching `dsn` fields. Without a password, libpq's
`PGPASSWORD` and `~/.pgpass` apply.

Each `Connection` keeps one server session, so `commit()` and `rollback()`
act on the statements run before them, as in `sqlite3`.

### Result types

Values come back as the Python types `sqlite3` returns for the same data, in
both embedded and daemon mode:

| Column type | Python value |
|-------------|--------------|
| `INTEGER`, `BIGINT`, `SMALLINT` | `int` |
| `REAL`, `FLOAT`, `DOUBLE` | `float` |
| `NUMERIC` / `DECIMAL` | `int` if integral and within 64 bits, else `float` (SQLite `NUMERIC` affinity) |
| `BOOLEAN` | `int` `1` / `0` (SQLite has no boolean type) |
| `BLOB` / `BYTEA` | `bytes` |
| `NULL` | `None` |
| `TEXT`, `VARCHAR`, `CHAR`, `DATE`, `TIME`, `TIMESTAMP`, `UUID`, `JSON`, other types | `str` (`TIMESTAMP` as `YYYY-MM-DD HH:MM:SS[.ffffff]`) |

How each mode knows the type:

- **Embedded** - the in-process engine hands back typed values, the way
  CPython's `sqlite3` reads `sqlite3_column_type()` for every value and
  rusqlite exposes `ValueRef::{Integer, Real, Text, Blob, Null}`. Nothing is
  parsed from text. Parameters are bound natively (`$1..$n`), never spliced
  into the SQL.
- **Daemon** - values arrive as text with the column type OID from the
  PostgreSQL RowDescription message and are converted by that OID, never by
  what the text looks like (the text `'7'` in a `TEXT` column stays `'7'`).

To get `datetime.date` and similar objects, pass `detect_types`, as with
`sqlite3`. The `date` and `timestamp` converters `sqlite3` registers are
registered here too. With `PARSE_DECLTYPES` a converter is looked up by the
first word of the column's declared type exactly as written in
`CREATE TABLE` (`INTEGER`, `REAL`, `DATETIME`, `BOOLEAN`, `DATE`,
`TIMESTAMP`, or a custom name such as `POINT`), like
`sqlite3_column_decltype()`; result columns that are expressions have no
declared type and get no converter, as in `sqlite3`. With `PARSE_COLNAMES`
a converter is looked up by a `[type]` suffix in the column alias.
Converters receive the stored value's `bytes` (`b'1'` for a true
`BOOLEAN`, `b'1.5'` for a `REAL`) and are never called for NULL.
`register_adapter()` adapters apply to parameters, and
`Connection.text_factory` to text values.

HeliosDB stores its own column types (`INTEGER` is created as `BIGINT`, see
"SQLite schemas"), so this layer records each column's declared type when it
runs `CREATE TABLE` / `ALTER TABLE ... ADD COLUMN`, in a small table named
`heliosdb_sqlite_decltypes` (much as SQLite keeps `sqlite_sequence`).
`connect(..., declared_types=False)` turns the recording off; for tables
created without it, converters are looked up by the engine's type
(`BIGINT`, `DOUBLE`, `TIMESTAMP`, ...).

Parameters follow `sqlite3`'s binding rules in both modes: `None`, `int`,
`float`, `str`, `bytes`/`bytearray`/`memoryview` (plus `date`/`datetime`,
`Decimal`, registered adapters, and lists of numbers for HeliosDB `VECTOR`
columns). Any other type (an `object`, `dict`, `set`, ...) raises the error
`sqlite3` raises (`ProgrammingError` on Python 3.11+, `InterfaceError`
before). `float('inf')` round-trips; `NaN` binds as `NULL`, as in `sqlite3`.

```python
conn = heliosdb_sqlite.connect('app.db', detect_types=heliosdb_sqlite.PARSE_DECLTYPES)
conn.execute("CREATE TABLE events (day DATE, at TIMESTAMP)")
conn.execute("INSERT INTO events VALUES (?, ?)", ('2026-10-07', '2026-10-07 12:34:56'))
conn.execute("SELECT day, at FROM events").fetchone()
# (datetime.date(2026, 10, 7), datetime.datetime(2026, 10, 7, 12, 34, 56))
```

`TIMESTAMP` / `DATETIME` values read without a converter come back as the
text that was stored (`'2026-10-07 12:34:56'`), in both modes.

The REPL fallback (no `heliosdb-nano-embedded`) returns every value as `str`
(`NULL` becomes `None`; a text value spelled `NULL` does too).

### SQLite schemas

SQLite stores every integer in 64 bits and every `REAL` as an 8-byte double,
whatever the declared type says, accepts any type name, and assigns
`INTEGER PRIMARY KEY` automatically. HeliosDB follows PostgreSQL types, so
`CREATE TABLE` and `ALTER TABLE ... ADD COLUMN` statements are mapped to keep
SQLite's meaning:

| Declared in SQLite | Created in HeliosDB |
|--------------------|---------------------|
| `INT`, `INTEGER`, `TINYINT`, `SMALLINT`, `MEDIUMINT`, `BIGINT`, `INT2`, `INT8` | `BIGINT` |
| `INTEGER PRIMARY KEY` (also `PRIMARY KEY (id)` on an `INTEGER` column, and `AUTOINCREMENT`) | `BIGINT PRIMARY KEY DEFAULT nextval('<table>_<column>_rowid_seq')` |
| `REAL`, `FLOAT`, `DOUBLE` | `DOUBLE PRECISION` |
| `BLOB` | `BYTEA` |
| `DATETIME` | `TIMESTAMP` |
| `BOOL` | `BOOLEAN` |
| any name HeliosDB does not know (`MYTYPE`, `MONEY`, `NCHAR(5)`, ...) | by SQLite's affinity rules: contains `INT` -> `BIGINT`; `CHAR`/`CLOB`/`TEXT` -> `TEXT`; `BLOB` -> `BYTEA`; `REAL`/`FLOA`/`DOUB` -> `DOUBLE PRECISION`; otherwise `TEXT` |

Types HeliosDB knows (`TEXT`, `VARCHAR(n)`, `NUMERIC(p,s)`, `BOOLEAN`,
`DATE`, `TIMESTAMP`, `UUID`, `JSON`, `VECTOR(n)`, arrays, ...) are created as
written. The declared names are kept for `PARSE_DECLTYPES` (see "Result
types"). Pass `sqlite_types=False` to send DDL unchanged.

`INTEGER PRIMARY KEY` behaves like SQLite's rowid alias: an `INSERT` that
omits the key, or gives `NULL` / `None` for it, gets one more than the
largest key used so far, also after rows inserted with explicit keys
(explicit `2` then automatic gives `3`; explicit `100` then automatic gives
`101`). The keys come from a per-table sequence that this layer moves past
every explicit key it inserts (including `executemany()` and `UPDATE ...
SET id = ...`); a new table of the same name starts again at `1`.

### Database files

In embedded mode each path is its own database, as in `sqlite3`:
`connect('app.db')` keeps its data in the directory `app.db/` (HeliosDB
stores a database as a directory, not a single file). `data_dir=` chooses
another directory. An existing SQLite file at the path is not opened; use a
new path or `data_dir=`.

Versions before 3.1.0 kept every database of a directory in one shared
`heliosdb-data/` directory next to the path. If that directory exists and
the database path does not, it is still used, with a `RuntimeWarning`; move
or rename it to the database path to give each database its own storage.

### Transactions

As in `sqlite3`: with the default `isolation_level`, a transaction opens
implicitly before the first `INSERT`/`UPDATE`/`DELETE`/`REPLACE` and lasts
until `commit()` or `rollback()`; `isolation_level=None` is autocommit, where
your own `BEGIN`/`COMMIT` apply. `Connection.in_transaction` reports the
state, `with conn:` commits or rolls back, and `executescript()` commits a
pending transaction first. A statement that fails inside a transaction (for
example with `IntegrityError`) leaves the transaction usable.

In embedded mode, `connect(':memory:')` gives each connection its own
database. Connections to the same database directory in one process share
the engine; while one of them has a transaction open, the others wait up to
`timeout` seconds and then raise `OperationalError: database is locked`.

### DB-API attributes

As in `sqlite3`: the module globals `apilevel` (`'2.0'`), `threadsafety`
(`1`: threads may share the module, not a connection) and `paramstyle`
(`'qmark'`; `:name`, `?NNN`, `@name` and `$name` work too), and
`Connection.total_changes`, the number of rows inserted, updated or deleted
through the connection since it was opened.

### Known differences from sqlite3

- Two result columns with the same name (`SELECT 1 AS a, 2 AS a`) collapse to
  one in embedded mode (the binding returns rows as dicts).
- `REAL` columns created by other tools as 4-byte `float4` return the
  shortest decimal that round-trips (`0.1`), not the widened double.
- Automatic `INTEGER PRIMARY KEY` values never reuse a key, even after the
  row with the largest key is deleted (SQLite without `AUTOINCREMENT` may
  reuse it). Keys written by another client directly (not through this
  layer) can make a later automatic key collide.
- Tables created before 3.1.0 with `INTEGER PRIMARY KEY` use the engine's
  `AUTOINCREMENT` counter, which does not move past explicit ids. Recreate
  such tables (`CREATE TABLE new ... ; INSERT INTO new SELECT * FROM old`)
  to get the behaviour above.
- Columns without a type (`CREATE TABLE t (a, b)`) are not accepted.
- SQLite-specific functions such as `typeof()` are not available.

### Exception Handling

```python
import heliosdb_sqlite

try:
    conn = heliosdb_sqlite.connect('mydb.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM nonexistent_table')

except heliosdb_sqlite.Error as e:
    print(f"Database error: {e}")

except heliosdb_sqlite.OperationalError as e:
    print(f"Operational error: {e}")

except heliosdb_sqlite.ProgrammingError as e:
    print(f"Programming error: {e}")

finally:
    if 'conn' in locals():
        conn.close()
```

---

## Platform Support

| Platform | Embedded mode | Daemon mode |
|----------|---------------|-------------|
| Linux x86_64 (glibc 2.28+) | In-process engine (`heliosdb-nano-embedded` wheel) | Yes |
| Other platforms | REPL fallback (text values) until a binding wheel is published | Yes |

### Python Version Support

- ✅ Python 3.8
- ✅ Python 3.9
- ✅ Python 3.10
- ✅ Python 3.11
- ✅ Python 3.12
- ✅ Python 3.13

---

## Performance

### Compression Ratios

| Data Type | Compression | Memory Savings |
|-----------|-------------|----------------|
| **768-dim vectors** | 384x | 3,072 bytes → 8 bytes |
| **512-dim vectors** | 256x | 2,048 bytes → 8 bytes |

### Query Performance

| Operation | Performance | Notes |
|-----------|-------------|-------|
| **Vector search** | 1K-5K QPS | With PQ compression |
| **Full table scan** | 500K-1M rows/sec | SIMD-accelerated |
| **Time-travel lookup** | <100ms | Indexed snapshots |

---

## Comparison with sqlite3

| Feature | sqlite3 | heliosdb-sqlite |
|---------|---------|-----------------|
| **sqlite3 DB-API** | ✅ | ✅ (see Known differences) |
| **Vector Search** | ❌ | ✅ Built-in HNSW + PQ |
| **Encryption** | ❌ | ✅ AES-256-GCM |
| **Time-Travel** | ❌ | ✅ AS OF queries |
| **Branching** | ❌ | ✅ Git-like workflows |
| **PostgreSQL Types** | ❌ | ✅ JSONB, UUID, VECTOR |
| **Performance** | Fast | Fast + SIMD |

---

## Documentation

- **Drop-in guide**: https://heliosdb.com/docs/nano/features/sqlite/heliosdb_sqlite_drop_in_guide/
- **Migration patterns**: https://heliosdb.com/docs/nano/features/sqlite/heliosdb_sqlite_migration_patterns/
- **Troubleshooting**: https://heliosdb.com/docs/nano/features/sqlite/heliosdb_sqlite_troubleshooting/
- **HeliosDB Nano docs**: https://heliosdb.com/docs/nano/

---

## Troubleshooting

### HeliosDB Nano executable not found

`InterfaceError: HeliosDB Nano executable not found` means neither
`$HELIOSDB_BINARY` nor `heliosdb-nano` on `PATH` could be found:

```bash
which heliosdb-nano            # should print a path
heliosdb-nano --version
export HELIOSDB_BINARY=/full/path/to/heliosdb-nano   # alternative to PATH
```

The official Linux release binaries need glibc 2.39 or newer. On older
distributions, run inside a newer base image such as `debian:trixie-slim`.

---

## Contributing

Contributions are welcome — open an issue or a pull request on [GitHub](https://github.com/HeliosDatabase/HeliosDB-SDKs).

### Development Setup

```bash
git clone https://github.com/HeliosDatabase/HeliosDB-SDKs.git
cd HeliosDB-SDKs/sdks/python-sqlite

# Install in development mode
pip install -e ".[dev]"

# Run tests; tests/test_sqlite3_conformance.py runs every case on CPython's
# sqlite3 and on heliosdb_sqlite and requires identical values and types
pytest tests/ -v

# Run the integration and conformance tests against a throwaway HeliosDB Nano server
# (Linux Docker host; the server is removed afterwards)
scripts/nano-integration-test.sh -v

# Format code
black heliosdb_sqlite/ tests/
ruff check heliosdb_sqlite/ tests/

# Type checking
mypy heliosdb_sqlite/
```

---

## License

Apache License 2.0 - see [LICENSE](https://github.com/HeliosDatabase/HeliosDB-SDKs/blob/main/LICENSE) for details.

---

## Support

- **GitHub Issues**: https://github.com/HeliosDatabase/HeliosDB-SDKs/issues
- **Discord**: https://discord.gg/yTykuUrFXc
- **Email**: support@heliosdb.com

---

## Acknowledgments

Built on top of:
- **HeliosDB** - PostgreSQL-compatible embedded database
- **RocksDB** - High-performance key-value store
- **Apache Arrow** - Columnar data format
- **HNSW** - Approximate nearest neighbor search

---

**Made with ❤️ for developers who need production-grade embedded databases**

[⭐ Star us on GitHub](https://github.com/HeliosDatabase/HeliosDB-SDKs)
