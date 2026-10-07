"""In-process embedded backend: HeliosDB Nano's PyO3 binding.

The ``heliosdb-nano-embedded`` wheel (import name ``heliosdb_nano``) links the
Nano engine into the Python process and hands back native Python values, the
way ``sqlite3`` hands back what ``sqlite3_column_type`` reports for each value
(INTEGER -> ``int``, REAL -> ``float``, TEXT -> ``str``, BLOB -> ``bytes``,
NULL -> ``None``). No subprocess, no text table to parse, no type guessing.

This module adapts that binding to the sqlite3 DB-API surface:

* parameters are bound natively (``$1..$n``), never spliced into SQL text;
* engine values are normalised to the types sqlite3 returns (``bool`` -> ``int``;
  ``NUMERIC`` -> ``int``/``float`` per SQLite's NUMERIC affinity; ``TIMESTAMP``
  rendered as SQL text ``YYYY-MM-DD HH:MM:SS[.ffffff]``);
* ``PRAGMA table_info`` is answered from ``information_schema``; other PRAGMAs
  the engine does not know are ignored, as SQLite ignores unknown pragmas;
* several Connections to one database directory in the same process share
  one engine handle (the directory is locked per process), with a
  database-level write lock so one Connection's open transaction never
  absorbs another Connection's statements.

Binding gaps worked around here are listed in the package README
("Embedded mode: typed results").
"""

import os
import re
import struct
import threading
from typing import Any, Dict, List, Optional, Sequence, Union

from . import _sql, _types

# data_type spellings in Nano's information_schema.columns -> wire type OID
# (the OIDs ``_types`` already knows, so PARSE_DECLTYPES works the same way in
# embedded and daemon mode).
_DATA_TYPE_OIDS = {
    'int2': _types.INT2,
    'int4': _types.INT4,
    'int8': _types.INT8,
    'float4': _types.FLOAT4,
    'float8': _types.FLOAT8,
    'numeric': _types.NUMERIC,
    'boolean': _types.BOOL,
    'bytea': _types.BYTEA,
    'text': _types.TEXT,
    'varchar': _types.VARCHAR,
    'char': _types.BPCHAR,
    'date': _types.DATE,
    'time': _types.TIME,
    'timestamp': _types.TIMESTAMP,
    'timestamptz': _types.TIMESTAMPTZ,
    'interval': _types.INTERVAL,
    'uuid': _types.UUID,
    'json': _types.JSON,
    'jsonb': _types.JSONB,
}

_ROW_KEYWORDS = frozenset(('SELECT', 'WITH', 'VALUES', 'SHOW', 'EXPLAIN', 'TABLE', 'DESCRIBE'))
_WRITE_KEYWORDS = frozenset(('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE', 'UPSERT'))
_DDL_KEYWORDS = frozenset(('CREATE', 'ALTER', 'DROP', 'TRUNCATE', 'RENAME'))
_EMPTY_PROBE_KEYWORDS = frozenset(('SELECT', 'WITH', 'VALUES'))
_PROBE_COLUMN = '__heliosdb_sqlite_probe'

_INT64_MIN = -(2 ** 63)
_INT64_MAX = 2 ** 63 - 1

# '2026-10-07T12:00:00.500+00:00' (chrono's RFC 3339 rendering)
_RFC3339_RE = re.compile(
    r'^(\d{4,}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$'
)

_PRAGMA_RE = re.compile(
    r'^\s*PRAGMA\s+(?:(?P<schema>\w+)\s*\.\s*)?(?P<name>\w+)\s*'
    r'(?:\(\s*(?P<arg>[^)]*?)\s*\)|=\s*(?P<value>.*?))?\s*;?\s*$',
    re.IGNORECASE | re.DOTALL,
)


def load_binding() -> Optional[Any]:
    """The ``heliosdb_nano`` module, or None when the wheel is not installed
    (or cannot load on this platform)."""
    try:
        import heliosdb_nano  # type: ignore[import-not-found]
    except Exception:  # ImportError, or an incompatible native library
        return None
    if not hasattr(heliosdb_nano, 'EmbeddedDatabase'):
        return None
    return heliosdb_nano


def binding_version(module: Any) -> str:
    return str(getattr(module, '__version__', 'unknown'))


def classify_error(message: str) -> str:
    """DB-API exception class name for an engine error message, following
    the classes sqlite3 raises for the equivalent SQLite errors."""
    text = message.lower()
    if 'multiple statements' in text:
        return 'ProgrammingError'
    if ('constraint' in text or 'duplicate key' in text or 'violates' in text
            or 'foreign key' in text):
        return 'IntegrityError'
    if 'cannot cast' in text or 'invalid digit' in text or 'out of range' in text:
        return 'DataError'
    return 'OperationalError'


# --------------------------------------------------------------------------
# Shared engine handles (one per database directory per process)
# --------------------------------------------------------------------------

class _Handle:
    def __init__(self, db: Any):
        self.db = db
        self.refs = 0
        # Held by the Connection that has a transaction open. Every other
        # Connection waits for it before running a statement, so its
        # statements never run inside someone else's transaction.
        self.write_lock = threading.Lock()


_HANDLES: Dict[str, _Handle] = {}
_HANDLES_LOCK = threading.Lock()


class EmbeddedBackend:
    """One sqlite3 Connection's view of an in-process Nano database."""

    def __init__(self, module: Any, path: Optional[str], timeout: float):
        self._module = module
        self._timeout = timeout
        self._owner = False   # holds the handle's write lock (open transaction)
        self._columns_cache: Dict[str, Dict[str, str]] = {}
        if path is None:
            # sqlite3 ':memory:': a private database per Connection.
            self._key = None
            self._handle = _Handle(module.EmbeddedDatabase.in_memory())
            self._handle.refs = 1
        else:
            key = os.path.abspath(path)
            with _HANDLES_LOCK:
                handle = _HANDLES.get(key)
                if handle is None:
                    os.makedirs(os.path.dirname(key) or '.', exist_ok=True)
                    handle = _Handle(module.EmbeddedDatabase(key))
                    _HANDLES[key] = handle
                handle.refs += 1
            self._key = key
            self._handle = handle

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        if self._owner:
            try:
                handle.db.execute('ROLLBACK')
            except Exception:
                pass
            self._release()
        with _HANDLES_LOCK:
            handle.refs -= 1
            if handle.refs <= 0 and self._key is not None:
                _HANDLES.pop(self._key, None)
        # Dropping the last reference closes the engine and its directory lock.
        handle.db = None

    def _db(self) -> Any:
        if self._handle is None or self._handle.db is None:
            raise RuntimeError('database handle is closed')
        return self._handle.db

    def _acquire(self) -> None:
        if self._owner or self._handle is None:
            return
        timeout = self._timeout if self._timeout and self._timeout > 0 else -1
        if not self._handle.write_lock.acquire(timeout=timeout):
            raise _LockTimeout('database is locked')
        self._owner = True

    def _release(self) -> None:
        if self._owner and self._handle is not None:
            self._owner = False
            self._handle.write_lock.release()

    # -- execution ---------------------------------------------------------

    def run(self, sql: str, params: Sequence[Any] = ()) -> Union[Dict[str, Any], int]:
        """Execute one statement. Returns ``{'rows', 'columns', 'decl_oids'}``
        for a statement that produces rows, otherwise the affected-row count
        (-1 when not applicable)."""
        keyword = _sql.first_keyword(sql)
        if keyword in ('BEGIN', 'START'):
            acquired = not self._owner
            self._acquire()
            try:
                return self._run(keyword, sql, params)
            except Exception:
                if acquired:
                    self._release()
                raise
        if keyword in ('COMMIT', 'END') or (
                keyword == 'ROLLBACK' and not _sql.has_keyword(sql, 'TO')):
            try:
                return self._run(keyword, sql, params)
            finally:
                # COMMIT / ROLLBACK end the transaction whether or not they
                # succeed (a failed COMMIT rolls back).
                self._release()
        if self._owner:
            return self._run(keyword, sql, params)
        # Not in a transaction: wait for any other Connection's transaction
        # to finish, then run the statement on its own.
        self._acquire()
        try:
            return self._run(keyword, sql, params)
        finally:
            self._release()

    def run_many(self, sql: str, batch: List[List[Any]]) -> int:
        """Execute one DML statement for every parameter list in ``batch``;
        returns the total affected-row count."""
        if self._owner:
            return int(self._db().execute_many(sql, batch))
        self._acquire()
        try:
            return int(self._db().execute_many(sql, batch))
        finally:
            self._release()

    def _run(self, keyword: str, sql: str, params: Sequence[Any]) -> Union[Dict[str, Any], int]:
        db = self._db()
        bound = list(params) if params else None
        if bound is None and keyword in _WRITE_KEYWORDS:
            # heliosdb-nano-embedded 4.31.1: execute(sql) without parameters
            # runs UPDATE / DELETE outside the open transaction's view, so
            # rows written earlier in the transaction are not matched (0
            # rows). The parameterised path is correct; an unused parameter
            # selects it.
            bound = [None]
        if keyword in _DDL_KEYWORDS:
            self._columns_cache.clear()
        if keyword == 'PRAGMA':
            return self._pragma(sql)
        if keyword in _ROW_KEYWORDS or _sql.has_keyword(sql, 'RETURNING'):
            rows = db.query(sql, bound)
            if rows:
                columns = list(rows[0].keys())
                values = [list(row.values()) for row in rows]
            else:
                columns = self._empty_result_columns(keyword, sql, bound)
                values = []
            decl_oids = self._decl_oids(sql, columns)
            return {
                'rows': [_normalise_row(row, decl_oids) for row in values],
                'columns': columns,
                'decl_oids': decl_oids,
            }
        count = db.execute(sql, bound)
        if keyword in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE', 'UPSERT'):
            return int(count)
        return -1

    def _empty_result_columns(self, keyword: str, sql: str,
                              params: Optional[List[Any]]) -> List[str]:
        """Column names of a query that returned no rows (the binding only
        reports names through row dicts). sqlite3 fills cursor.description
        for an empty result, so ask the engine for one all-NULL row of the
        same shape: LEFT JOIN the query to a one-row relation."""
        if keyword not in _EMPTY_PROBE_KEYWORDS:
            return []
        body = sql.strip()
        while body.endswith(';'):
            body = body[:-1].rstrip()
        probe = (f'SELECT _hq.* FROM (SELECT 1 AS {_PROBE_COLUMN}) AS _hp '
                 f'LEFT JOIN (\n{body}\n) AS _hq ON TRUE')
        try:
            rows = self._db().query(probe, params)
        except Exception:
            return []
        if not rows:
            return []
        return [name for name in rows[0].keys() if name != _PROBE_COLUMN]

    # -- column types from the catalog ------------------------------------

    def _table_columns(self, table: str) -> Dict[str, str]:
        """``{column name (lower): data_type (lower)}`` for ``table``."""
        cached = self._columns_cache.get(table)
        if cached is not None:
            return cached
        try:
            rows = self._db().query(
                'SELECT column_name, data_type FROM information_schema.columns '
                'WHERE table_name = $1', [table])
        except Exception:
            rows = []
        columns = {}
        for row in rows:
            name = row.get('column_name')
            data_type = row.get('data_type')
            if isinstance(name, str) and isinstance(data_type, str):
                columns[name.lower()] = _base_data_type(data_type)
        self._columns_cache[table] = columns
        return columns

    def _decl_oids(self, sql: str, columns: List[str]) -> List[Optional[int]]:
        """The declared type (as a type OID) of each result column that is a
        plain column reference to a table in the statement, like
        ``sqlite3_column_decltype``; None for expressions and unknown
        columns."""
        if not columns:
            return []
        tables = _sql.referenced_tables(sql)
        if not tables:
            return [None] * len(columns)
        known = [self._table_columns(t) for t in tables]
        oids: List[Optional[int]] = []
        for name in columns:
            types = {cols[name.lower()] for cols in known
                     if isinstance(name, str) and name.lower() in cols}
            oids.append(_DATA_TYPE_OIDS.get(types.pop()) if len(types) == 1 else None)
        return oids

    # -- PRAGMA ------------------------------------------------------------

    def _pragma(self, sql: str) -> Union[Dict[str, Any], int]:
        m = _PRAGMA_RE.match(sql)
        name = m.group('name').lower() if m else ''
        if name in ('table_info', 'table_xinfo'):
            arg = (m.group('arg') or m.group('value') or '').strip().strip('\'"`[]')
            return self._table_info(arg)
        try:
            rows = self._db().query(sql)
        except Exception:
            # SQLite ignores pragmas it does not know; so do we.
            return -1
        if not rows:
            return -1
        columns = list(rows[0].keys())
        return {'rows': [_normalise_row(list(r.values()), None) for r in rows],
                'columns': columns, 'decl_oids': [None] * len(columns)}

    def _table_info(self, table: str) -> Dict[str, Any]:
        columns = ['cid', 'name', 'type', 'notnull', 'dflt_value', 'pk']
        table = table.split('.')[-1]
        db = self._db()
        lookup = table if table.startswith('"') else table.lower()
        lookup = lookup.strip('"')
        try:
            cols = db.query(
                'SELECT column_name, udt_name, data_type, is_nullable, column_default, '
                'ordinal_position FROM information_schema.columns WHERE table_name = $1',
                [lookup])
        except Exception:
            cols = []
        pk_cols: Dict[str, int] = {}
        try:
            constraints = db.query(
                'SELECT constraint_name, constraint_type FROM '
                'information_schema.table_constraints WHERE table_name = $1', [lookup])
            pk_names = {c['constraint_name'] for c in constraints
                        if c.get('constraint_type') == 'PRIMARY KEY'}
            if pk_names:
                usage = db.query(
                    'SELECT constraint_name, column_name, ordinal_position FROM '
                    'information_schema.key_column_usage WHERE table_name = $1', [lookup])
                for u in usage:
                    if u.get('constraint_name') in pk_names:
                        pk_cols.setdefault(u['column_name'], int(u.get('ordinal_position') or 1))
        except Exception:
            pass
        cols = sorted(cols, key=lambda c: c.get('ordinal_position') or 0)
        rows = []
        for cid, c in enumerate(cols):
            type_name = (c.get('udt_name') or c.get('data_type') or '').upper()
            rows.append([
                cid,
                c.get('column_name'),
                type_name,
                1 if c.get('is_nullable') == 'NO' else 0,
                c.get('column_default'),
                pk_cols.get(c.get('column_name'), 0),
            ])
        return {'rows': rows, 'columns': columns, 'decl_oids': [None] * len(columns)}


class _LockTimeout(Exception):
    """Another Connection kept its transaction open past the timeout."""


# --------------------------------------------------------------------------
# Value normalisation (engine value -> sqlite3 value)
# --------------------------------------------------------------------------

def _base_data_type(data_type: str) -> str:
    """'Varchar(Some(10))' -> 'varchar', 'Int4' -> 'int4'."""
    return data_type.split('(', 1)[0].strip().lower()


def _sql_timestamp(text: str, with_zone: bool) -> str:
    """chrono's RFC 3339 rendering -> the SQL text form sqlite3 users store
    and the PostgreSQL wire protocol sends:
    '2026-10-07T12:00:00.500+00:00' -> '2026-10-07 12:00:00.5'."""
    m = _RFC3339_RE.match(text)
    if not m:
        return text
    date, clock, frac, zone = m.groups()
    frac = (frac or '').rstrip('0')
    if frac == '.':
        frac = ''
    out = f'{date} {clock}{frac}'
    if with_zone:
        if zone == 'Z':
            zone = '+00:00'
        if zone.endswith(':00'):
            zone = zone[:-3]
        out += zone
    return out


def _shortest_float4(value: float) -> float:
    """A REAL (float4) widened to a Python float: 0.1f becomes
    0.10000000149011612. Return the shortest decimal that round-trips
    through float4 instead (0.1), the value the PostgreSQL wire protocol
    sends for the same column."""
    if value != value or value in (float('inf'), float('-inf')):
        return value
    try:
        target = struct.pack('<f', value)
    except OverflowError:
        return value
    for digits in range(6, 10):
        candidate = float(f'{value:.{digits}g}')
        if struct.pack('<f', candidate) == target:
            return candidate
    return value


def _normalise_value(value: Any, decl_oid: Optional[int]) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0  # SQLite has no boolean storage class
    if isinstance(value, float) and decl_oid == _types.FLOAT4:
        return _shortest_float4(value)
    if isinstance(value, str) and decl_oid is not None:
        try:
            if decl_oid == _types.NUMERIC:
                return _types._to_numeric(value)
            if decl_oid == _types.TIMESTAMP:
                return _sql_timestamp(value, with_zone=False)
            if decl_oid == _types.TIMESTAMPTZ:
                return _sql_timestamp(value, with_zone=True)
        except ValueError:
            return value
    return value


def _normalise_row(row: List[Any], decl_oids: Optional[List[Optional[int]]]) -> List[Any]:
    if not decl_oids:
        return [_normalise_value(v, None) for v in row]
    return [
        _normalise_value(v, decl_oids[i] if i < len(decl_oids) else None)
        for i, v in enumerate(row)
    ]


def converter_input(value: Any) -> bytes:
    """The bytes a register_converter() callable receives for a typed value,
    as sqlite3 passes the stored value's bytes."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, float):
        return repr(value).encode('ascii')
    return str(value).encode('utf-8')


def check_int64(value: int) -> int:
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise OverflowError('Python int too large to convert to SQLite INTEGER')
    return value
