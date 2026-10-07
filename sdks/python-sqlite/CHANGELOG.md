# Changelog

All notable changes to `heliosdb-sqlite` are listed here.

## 3.1.0 (unreleased)

### Fixed

- Embedded mode returns `sqlite3` result types: `SELECT id, code` gives
  `(7, 'A2')`, not `('7', 'A2')`. Embedded mode now runs HeliosDB Nano in
  process through the `heliosdb-nano-embedded` binding (a dependency on Linux
  x86_64), which returns typed values, instead of parsing the text table
  `heliosdb-nano repl` prints. Without the binding it falls back to the REPL
  with a `RuntimeWarning`; `embedded_backend='binding'|'repl'|'auto'` (or
  `HELIOSDB_SQLITE_BACKEND`) chooses explicitly.
- Parameters are bound natively in embedded mode (`$1..$n`) and, in every
  mode, placeholders inside string literals, quoted identifiers and comments
  are no longer replaced. `?NNN` placeholders, `@name`/`$name`, and
  `ProgrammingError` for a wrong number of bindings follow `sqlite3`; `bool`
  binds as `1`/`0`; an `int` outside 64 bits raises `OverflowError`.
- Transactions follow `sqlite3`: no transaction is opened at connect time;
  one starts implicitly before `INSERT`/`UPDATE`/`DELETE`/`REPLACE` and again
  after each `commit()` (previously everything after the first `commit()`
  ran in autocommit). New `Connection.in_transaction`. In daemon mode a
  failing statement no longer aborts the whole transaction (each statement
  inside a transaction runs under a savepoint; `statement_savepoints=False`
  turns that off).
- `cursor.rowcount` is `-1` after a `SELECT`, as in `sqlite3`; `executemany()`
  reports the total, rejects queries with `ProgrammingError`, and leaves
  `lastrowid` unchanged. `executescript()` commits a pending transaction
  first and splits statements without breaking on `;` inside strings.
- Engine errors raise the `sqlite3` classes: constraint violations
  `IntegrityError`, syntax errors and missing tables `OperationalError` (the
  daemon used to raise `ProgrammingError`), instead of a bare
  `DatabaseError`.
- `Row` name lookup is case-insensitive and rows compare equal, as
  `sqlite3.Row`.
- `INTEGER PRIMARY KEY` is assigned as in SQLite after rows with explicit
  keys: explicit `2` then an `INSERT` without a key gives `3` (embedded mode
  stored a second row with key `2`; daemon mode raised `IntegrityError`), and
  explicit `100` then automatic gives `101` (both modes gave `7`). The key
  column now draws from a per-table sequence (`<table>_<column>_rowid_seq`)
  that the layer moves past each explicit key it inserts (also through
  `executemany()`, `INSERT ... SELECT` and `UPDATE ... SET id = ...`). `NULL` /
  `None` for the key assigns one, as in SQLite. `PRIMARY KEY (id)` on an
  `INTEGER` column is treated the same way.
- `PARSE_DECLTYPES` looks converters up by the declared type as written in
  `CREATE TABLE` (`INTEGER`, `REAL`, `DATETIME`, custom names such as
  `POINT`), as `sqlite3` does. Previously the name was rebuilt from the
  engine's type (`BIGINT`, `DOUBLE`, `TIMESTAMP`, `TEXT`), so converters for
  `INTEGER`, `REAL` and custom names were never called and `DATETIME` got the
  built-in `timestamp` converter. Declared types are recorded in the
  `heliosdb_sqlite_decltypes` table (`declared_types=False` turns that off).
  Expression columns get no converter, as in `sqlite3`.
- Type names HeliosDB does not know (`MYTYPE`, `MONEY`, `NCHAR(5)`, ...) no
  longer fail `CREATE TABLE`; they are mapped by SQLite's affinity rules.
  `BOOL` becomes `BOOLEAN`.
- Parameters of types `sqlite3` cannot bind (`object()`, `dict`, `set`, ...)
  raise the error `sqlite3` raises (`ProgrammingError` on Python 3.11+,
  `InterfaceError` before) in both modes. Daemon mode used to store their
  `repr()` text. `float('inf')` works in daemon mode, and `NaN` binds as
  `NULL` in both modes, as in `sqlite3`.
- Converters registered for `BOOLEAN` receive `b'1'` / `b'0'` in both modes
  (daemon mode passed `b't'`), and `TIMESTAMP` text read without converters is
  the stored text in both modes (daemon mode padded it to `.000000`).
- Each database path is its own database in embedded mode: `connect('a.db')`
  and `connect('b.db')` in one directory used to share one `heliosdb-data/`
  directory and see each other's tables. The path itself is now the engine's
  data directory. An existing shared `heliosdb-data/` is still used, with a
  `RuntimeWarning`, when the path does not exist.
- Embedded mode works around a `heliosdb-nano-embedded` 4.31.1 issue where an
  `UPDATE` / `DELETE` without parameters inside a transaction did not see rows
  written earlier in that transaction (0 rows changed).
- `INSERT ... SELECT` into a table with an integer primary key no longer fails
  with a parse error (no `RETURNING` is appended to it; `lastrowid` is
  `None`).

### Added

- SQLite schemas keep their meaning: in `CREATE TABLE` / `ALTER TABLE ... ADD
  COLUMN`, integer types become `BIGINT` (64-bit), `INTEGER PRIMARY KEY`
  becomes a 64-bit key assigned when omitted (see Fixed),
  `REAL`/`FLOAT`/`DOUBLE` become `DOUBLE PRECISION`, `BLOB` becomes `BYTEA`
  and `DATETIME` becomes `TIMESTAMP`. `sqlite_types=False` disables this.
- Embedded mode answers `PRAGMA table_info(...)` and ignores other pragmas
  the engine does not know, as SQLite ignores unknown pragmas.
- `cursor.description` is filled for queries that return no rows.
- The DB-API module globals `apilevel`, `threadsafety` and `paramstyle`, and
  `Connection.total_changes`.
- The default `date` and `timestamp` converters of `sqlite3`, and
  `Connection.text_factory`.
- `tests/test_sqlite3_conformance.py`: every case runs on CPython's `sqlite3`
  and on `heliosdb_sqlite` (embedded and daemon) and must give identical
  values and types. `scripts/nano-integration-test.sh` runs it too.

### Fixed (daemon mode)

- Daemon mode returns `sqlite3` result types. Values are converted by the
  column type OID the server sends in the PostgreSQL RowDescription message:
  `SMALLINT`/`INTEGER`/`BIGINT` to `int`, `REAL`/`DOUBLE PRECISION` to
  `float`, `NUMERIC` to `int` when integral (else `float`), `BOOLEAN` to
  `1`/`0`, `BYTEA` to `bytes`, NULL to `None`, and every other type
  (`TEXT`, `DATE`, `TIMESTAMP`, `UUID`, `JSON`, ...) to the `str` the server
  sent: `SELECT id, code` returns `(7, 'A2')`, not `('7', 'A2')`.
  Previously psycopg2's own types leaked through (`True`, `Decimal`,
  `memoryview`, `datetime`, `dict`); those now follow `sqlite3` as well.
- Daemon mode keeps one server session per `Connection`. It used to open a
  new connection for every statement and close it without committing, so
  writes were rolled back and `commit()` acted on an empty session.
- Daemon mode can authenticate: new `dsn`, `server_user`, `server_password`
  and `server_database` options (it used to send user `helios` with an empty
  password, which also blocked `PGPASSWORD` and `~/.pgpass`). Server errors
  raise this module's `IntegrityError`, `OperationalError`, ... instead of
  psycopg2's classes, and a failed connection raises `OperationalError`.
- `bytes`, `bytearray` and `memoryview` parameters are sent as `'\x..'::bytea`
  literals; HeliosDB rejects SQLite's `X'..'` blob literal.

### Added (daemon mode)

- `detect_types=PARSE_DECLTYPES` applies `register_converter()` converters by
  the column's server type in daemon mode; `PARSE_COLNAMES` applies them by a
  `"alias [type]"` column alias and trims the alias in `cursor.description`.
- `daemon` extra (`psycopg2-binary`).
- `scripts/nano-integration-test.sh` runs the integration tests against a
  throwaway HeliosDB Nano container; they are skipped unless
  `HELIOSDB_TEST_URL` is set.

### Known limitations

- Embedded mode returned every value as `str` (fixed in 3.1.0).

## 3.0.1

- Ship the working `heliosdb_sqlite` implementation, including
  `cursor.lastrowid`, as the `heliosdb-sqlite` distribution.
- Find the engine as `$HELIOSDB_BINARY`, `heliosdb-nano` or `heliosdb` on
  `PATH`, or a bundled binary.
