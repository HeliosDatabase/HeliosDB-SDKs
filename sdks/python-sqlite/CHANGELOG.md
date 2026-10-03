# Changelog

All notable changes to `heliosdb-sqlite` are listed here.

## Unreleased

### Fixed

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

### Added

- `detect_types=PARSE_DECLTYPES` applies `register_converter()` converters by
  the column's server type in daemon mode; `PARSE_COLNAMES` applies them by a
  `"alias [type]"` column alias and trims the alias in `cursor.description`.
- `daemon` extra (`psycopg2-binary`).
- `scripts/nano-integration-test.sh` runs the integration tests against a
  throwaway HeliosDB Nano container; they are skipped unless
  `HELIOSDB_TEST_URL` is set.

### Known limitations

- Embedded mode still returns every value as `str`: it reads the table that
  `heliosdb-nano repl` prints, which has no column types. Use daemon mode, or
  hybrid mode after `switch_to_server()`, for typed values.

## 3.0.1

- Ship the working `heliosdb_sqlite` implementation, including
  `cursor.lastrowid`, as the `heliosdb-sqlite` distribution.
- Find the engine as `$HELIOSDB_BINARY`, `heliosdb-nano` or `heliosdb` on
  `PATH`, or a bundled binary.
