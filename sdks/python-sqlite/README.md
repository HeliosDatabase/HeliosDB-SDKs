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
- **Zero Python Dependencies** - Pure Python; talks to a local HeliosDB Nano executable (daemon mode, which connects to a running server, needs `psycopg2`)
- **Cross-Platform** - Linux, macOS, Windows support

---

## Installation

`heliosdb-sqlite` is not yet published on PyPI; install it from the
[HeliosDB-SDKs](https://github.com/HeliosDatabase/HeliosDB-SDKs) repository.

### Requirement: the HeliosDB Nano executable

The package is pure Python and drives a local `heliosdb-nano` process
(`heliosdb-nano repl`). Download `heliosdb-nano` from the
[HeliosDB-Nano releases](https://github.com/HeliosDatabase/HeliosDB-Nano/releases)
and put it on your `PATH`, or point `HELIOSDB_BINARY` at it:

```bash
export HELIOSDB_BINARY=/opt/heliosdb/heliosdb-nano
```

Lookup order: `$HELIOSDB_BINARY`, `heliosdb-nano` on `PATH`, `heliosdb` on
`PATH`, then a binary bundled under `heliosdb_sqlite/binaries/` (none is
bundled by the source install below).

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
# Output: 3.0.1

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
print(cursor.fetchall())  # [('1', 'Alice')]: embedded mode returns text,
                          # daemon mode [(1, 'Alice')]; see "Result types"

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

After an `INSERT` into a table whose primary key is an integer column
(`INTEGER`, `BIGINT`, `SERIAL`, ...), `cursor.lastrowid` holds the key of
the inserted row, as in `sqlite3`. For a multi-row `INSERT` it is the key of
the last row. Tables without an integer primary key leave it `None`.

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

Values come back as the Python types `sqlite3` returns for the same data,
decided by the column type the server reports (the type OID in the
PostgreSQL RowDescription message), never by what the text looks like:

| Server column type | Python value |
|--------------------|--------------|
| `SMALLINT`, `INTEGER`, `BIGINT` | `int` |
| `REAL`, `DOUBLE PRECISION` | `float` |
| `NUMERIC` / `DECIMAL` | `int` if integral and within 64 bits, else `float` (SQLite `NUMERIC` affinity) |
| `BOOLEAN` | `int` `1` / `0` (SQLite has no boolean type) |
| `BYTEA` | `bytes` |
| `NULL` | `None` |
| `TEXT`, `VARCHAR`, `CHAR`, `DATE`, `TIME`, `TIMESTAMP`, `UUID`, `JSON`, `VECTOR`, arrays, other types | `str`, as the server sent it |

To get `datetime.date` and similar objects, register a converter and pass
`detect_types`, as with `sqlite3`. With `PARSE_DECLTYPES` the converter is
looked up by the column's server type (`DATE`, `TIMESTAMP`, `INTEGER`,
`BOOLEAN`, `BYTEA`/`BLOB`, ...); with `PARSE_COLNAMES` by a `[type]` suffix in
the column alias. Converters receive `bytes` and are never called for NULL.

```python
heliosdb_sqlite.register_converter('DATE', lambda b: datetime.date.fromisoformat(b.decode()))
conn = heliosdb_sqlite.connect('app', mode='daemon', dsn=..., detect_types=heliosdb_sqlite.PARSE_DECLTYPES)
```

**Embedded mode returns text.** The embedded transport reads the table that
`heliosdb-nano repl` prints, which carries no column types, so every value
is a `str` (`NULL` becomes `None`; a text value spelled `NULL` does too).
`PARSE_COLNAMES` converters still apply. Use daemon mode, or hybrid mode
after `switch_to_server()`, when you need typed values.

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

| Platform | Architecture | Status | Wheel Available |
|----------|--------------|--------|-----------------|
| **Linux** | x86_64 | ✅ Stable | ✅ manylinux2014 |
| **Linux** | aarch64 | ✅ Stable | ✅ manylinux2014 |
| **macOS** | x86_64 (Intel) | ✅ Stable | ✅ 10.12+ |
| **macOS** | arm64 (Apple Silicon) | ✅ Stable | ✅ 11.0+ |
| **Windows** | x86_64 | ✅ Stable | ✅ Win10+ |

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
| **SQLite API** | ✅ | ✅ 100% compatible |
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

# Run tests
pytest tests/ -v

# Run the integration tests against a throwaway HeliosDB Nano server
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
