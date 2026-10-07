"""
HELIOSDB_SQLITE_MAIN_LIBRARY.py

Production-ready SQLite API compatibility layer for HeliosDB.
Provides the API of Python's sqlite3 module on top of HeliosDB Nano.

Architecture:
- Drop-in replacement: No application code changes required
- Embedded mode (default): the engine runs in-process through the
  ``heliosdb-nano-embedded`` binding (typed values, native parameter binding);
  without that wheel it falls back, with a warning, to driving the
  ``heliosdb-nano repl`` subprocess (every value comes back as text)
- Daemon mode: PostgreSQL wire protocol to a Nano server (typed by OID)
- Advanced features: Vector search, branching, time-travel, encryption
- Full transaction support: Autocommit, explicit transactions, savepoints
- Complete API coverage: All sqlite3.Connection and sqlite3.Cursor methods

Author: HeliosDB Team
Version: 3.1.0
License: Apache-2.0
"""

import sys
import os
import subprocess
import json
import threading
import time
import tempfile
import re
import warnings
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, List, Dict, Optional, Tuple, Union, Callable, Iterator
from datetime import date, time as datetime_time, datetime
from pathlib import Path

from . import _embedded, _sql, _types

# Version constants (mimics sqlite3)
version = "3.1.0"
version_info = (3, 1, 0)
sqlite_version = "3.45.0 (HeliosDB compatible)"
sqlite_version_info = (3, 45, 0)

# DB-API 2.0 (PEP 249) module globals, as in sqlite3
apilevel = "2.0"
threadsafety = 1      # threads may share the module, not connections
paramstyle = "qmark"  # ?, plus :name / ?NNN / @name / $name as in sqlite3

# Parse constants
PARSE_DECLTYPES = 1
PARSE_COLNAMES = 2

# Return codes
SQLITE_OK = 0
SQLITE_ERROR = 1
SQLITE_DENY = 1
SQLITE_IGNORE = 2

# Type adapters and converters (global registries)
_adapters = {}
_converters = {}
_trace_callback = None
_enable_callback_tracebacks_flag = False


# ============================================================================
# EXCEPTION HIERARCHY - Matches sqlite3 module
# ============================================================================

class Error(Exception):
    """Base class for all HeliosDB SQLite exceptions."""
    pass


class Warning(Exception):
    """Exception raised for important warnings."""
    pass


class InterfaceError(Error):
    """Exception raised for errors related to the database interface."""
    pass


class DatabaseError(Error):
    """Exception raised for errors related to the database."""
    pass


class InternalError(DatabaseError):
    """Exception raised when the database encounters an internal error."""
    pass


class OperationalError(DatabaseError):
    """Exception raised for errors related to database operation."""
    pass


class ProgrammingError(DatabaseError):
    """Exception raised for programming errors."""
    pass


class IntegrityError(DatabaseError):
    """Exception raised when database integrity is violated."""
    pass


class DataError(DatabaseError):
    """Exception raised for errors in the processed data."""
    pass


class NotSupportedError(DatabaseError):
    """Exception raised for unsupported operations."""
    pass


# DB-API exception class names (as used by psycopg2) -> this module's classes
_DRIVER_ERRORS = {
    'IntegrityError': IntegrityError,
    'ProgrammingError': ProgrammingError,
    'OperationalError': OperationalError,
    'DataError': DataError,
    'NotSupportedError': NotSupportedError,
    'InternalError': InternalError,
    'InterfaceError': InterfaceError,
}


# ============================================================================
# TYPE ADAPTERS - Binary, Date, Time, Timestamp
# ============================================================================

def _resolve_binary() -> str:
    """Locate the HeliosDB Nano executable used by embedded/hybrid modes.

    Order: $HELIOSDB_BINARY, ``heliosdb-nano`` on PATH, legacy ``heliosdb``
    on PATH, then a binary bundled in the package's ``binaries/`` directory.
    """
    import shutil
    explicit = os.environ.get('HELIOSDB_BINARY')
    if explicit:
        return explicit
    for name in ('heliosdb-nano', 'heliosdb'):
        found = shutil.which(name)
        if found:
            return found
    try:
        from .utils import get_binary_path
        return str(get_binary_path())
    except Exception:
        pass
    raise InterfaceError(
        "HeliosDB Nano executable not found. Install heliosdb-nano "
        "(https://github.com/HeliosDatabase/HeliosDB-Nano/releases) and put it "
        "on PATH, or set HELIOSDB_BINARY to its full path."
    )


def Binary(data: bytes) -> bytes:
    """Construct binary data for SQL insertion."""
    return data


def Date(year: int, month: int, day: int) -> date:
    """Construct a date object."""
    return date(year, month, day)


def Time(hour: int, minute: int, second: int) -> datetime_time:
    """Construct a time object."""
    return datetime_time(hour, minute, second)


def Timestamp(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    """Construct a timestamp object."""
    return datetime(year, month, day, hour, minute, second)


def DateFromTicks(ticks: float) -> date:
    """Construct a date from UNIX timestamp."""
    return datetime.fromtimestamp(ticks).date()


def TimeFromTicks(ticks: float) -> datetime_time:
    """Construct a time from UNIX timestamp."""
    return datetime.fromtimestamp(ticks).time()


def TimestampFromTicks(ticks: float) -> datetime:
    """Construct a timestamp from UNIX timestamp."""
    return datetime.fromtimestamp(ticks)


# ============================================================================
# ADAPTER/CONVERTER REGISTRATION
# ============================================================================

def register_adapter(type_: type, callable_: Callable) -> None:
    """Register a callable to convert Python type to SQL."""
    _adapters[type_] = callable_


def register_converter(typename: str, callable_: Callable) -> None:
    """Register a callable to convert SQL type to Python."""
    _converters[typename.upper()] = callable_


def register_trace_callback(callback: Optional[Callable]) -> None:
    """Register a callback for SQL statement tracing."""
    global _trace_callback
    _trace_callback = callback


def enable_callback_tracebacks(flag: bool) -> None:
    """Enable/disable traceback printing for callbacks."""
    global _enable_callback_tracebacks_flag
    _enable_callback_tracebacks_flag = flag


def complete_statement(statement: str) -> bool:
    """Check if SQL statement is complete (ends with semicolon)."""
    stripped = statement.strip()
    return stripped.endswith(';')


def _convert_date(val: bytes) -> date:
    return date(*map(int, val.split(b"-")))


def _convert_timestamp(val: bytes) -> datetime:
    datepart, timepart = val.split(b" ")
    year, month, day = map(int, datepart.split(b"-"))
    timepart_full = timepart.split(b".")
    hours, minutes, seconds = map(int, timepart_full[0].split(b":"))
    if len(timepart_full) == 2:
        microseconds = int('{:0<6.6}'.format(timepart_full[1].decode()))
    else:
        microseconds = 0
    return datetime(year, month, day, hours, minutes, seconds, microseconds)


# The default converters CPython's sqlite3 registers (used only with
# detect_types=PARSE_DECLTYPES / PARSE_COLNAMES).
register_converter("date", _convert_date)
register_converter("timestamp", _convert_timestamp)


# ============================================================================
# ROW CLASS - Factory for result rows
# ============================================================================

class Row:
    """
    A result row with access by index and by case-insensitive column name,
    like sqlite3.Row.
    """

    def __init__(self, cursor: 'Cursor', values: Tuple[Any, ...]):
        self._values = tuple(values)
        self._description = cursor.description or []

    def __getitem__(self, key: Union[int, str, slice]) -> Any:
        if isinstance(key, (int, slice)):
            return self._values[key]
        if isinstance(key, str):
            folded = key.lower()
            for i, desc in enumerate(self._description):
                if isinstance(desc[0], str) and desc[0].lower() == folded:
                    return self._values[i]
            raise IndexError("No item with that key")
        raise TypeError(f"Index must be int or str, not {type(key).__name__}")

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Row):
            return NotImplemented
        return self.keys() == other.keys() and self._values == other._values

    def __hash__(self) -> int:
        return hash((tuple(self.keys()), self._values))

    def __repr__(self) -> str:
        return f"<Row {self._values}>"

    def keys(self) -> List[str]:
        """Return list of column names."""
        return [desc[0] for desc in self._description]


# ============================================================================
# CURSOR CLASS - Executes SQL and manages results
# ============================================================================

class Cursor:
    """
    Database cursor for executing SQL statements and fetching results.
    Fully compatible with sqlite3.Cursor API.
    """

    # Pre-compiled patterns for the INSERT detector. Anchored at the start
    # so we don't trip on `WITH ... INSERT INTO ...` CTEs (which don't need
    # rewriting because the user already supplied RETURNING semantics) and
    # so multi-statement strings sent through `executescript` don't double-
    # count.
    _INSERT_RE = re.compile(
        r'^\s*INSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO\s+'
        r'("(?P<qname>[^"]+)"|(?P<pname>[A-Za-z_][\w\.]*))',
        re.IGNORECASE | re.DOTALL,
    )
    _RETURNING_RE = re.compile(r'\bRETURNING\b', re.IGNORECASE)

    def __init__(self, connection: 'Connection'):
        """
        Initialize cursor.

        Args:
            connection: Parent Connection object
        """
        self.connection = connection
        self.arraysize = 1
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self._results = []
        self._result_index = 0
        self.row_factory = None

    def _resolve_lastrowid_pk(self, table: str) -> Optional[str]:
        """Find the integer-typed primary-key column for `table`, if any.

        Result is cached on the parent Connection across cursors so a busy
        loop of inserts doesn't pay the catalog round-trip per row.
        Returns ``None`` for tables with no PK, a non-integer PK
        (e.g. TEXT keys), or when the catalog lookup fails.
        """
        cache = self.connection._lastrowid_pk_cache
        if table in cache:
            return cache[table]

        # Run PRAGMA table_info via a fresh internal cursor to avoid
        # clobbering our own state. The PRAGMA path is short-circuited by
        # the engine and never re-enters this rewriter (PRAGMA != INSERT).
        try:
            probe = Cursor(self.connection)
            probe.execute(f"PRAGMA table_info({table})")
            pk_col: Optional[str] = None
            for row in probe._results:
                # PRAGMA shape: (cid, name, type, notnull, dflt_value, pk)
                # Both the embedded REPL and the daemon path return strings
                # for the type field; treat anything containing INT as a
                # candidate. Composite PKs pick the first int column.
                try:
                    is_pk = int(row[5]) if row[5] is not None else 0
                except (TypeError, ValueError):
                    is_pk = 0
                if not is_pk:
                    continue
                col_type = (row[2] or '').upper()
                if 'INT' in col_type or 'SERIAL' in col_type:
                    pk_col = row[1]
                    break
            cache[table] = pk_col
            return pk_col
        except Exception:
            cache[table] = None
            return None

    def _maybe_inject_returning(self, sql: str) -> Tuple[str, Optional[str]]:
        """Rewrite `INSERT INTO t (...) VALUES (...)` →
        `INSERT INTO t (...) VALUES (...) RETURNING <pk>` so the engine
        echoes the inserted PK back. Caller stores the returned value as
        `cursor.lastrowid`.

        Returns ``(sql, pk_column or None)``. ``pk_column`` is None when no
        rewrite was applied (already has RETURNING, or the table has no
        int PK).
        """
        if self.connection._lastrowid_disabled:
            return sql, None
        m = self._INSERT_RE.match(sql)
        if not m:
            return sql, None
        if self._RETURNING_RE.search(sql):
            return sql, None
        info = _sql.parse_insert(sql)
        if info is not None and info.rows is None and not info.default_values:
            # INSERT ... SELECT: HeliosDB does not accept RETURNING here.
            return sql, None
        table = m.group('qname') or m.group('pname') or ''
        # Drop schema prefix (`public.users` → `users`); PRAGMA table_info
        # is name-only.
        table = table.rsplit('.', 1)[-1]
        if not table:
            return sql, None
        pk_col = self._resolve_lastrowid_pk(table)
        if not pk_col:
            return sql, None
        # Strip a trailing ';' before appending RETURNING — the SDK adds
        # the semicolon back on the wire. Anything else after the values
        # list (ON CONFLICT, etc.) is preserved as-is.
        body = sql.rstrip().rstrip(';')
        return f'{body} RETURNING "{pk_col}"', pk_col

    # Statements whose result set is a query result: sqlite3 reports
    # rowcount -1 for them.
    _QUERY_KEYWORDS = frozenset(('SELECT', 'WITH', 'VALUES', 'PRAGMA', 'SHOW', 'EXPLAIN', 'TABLE'))
    # Statements before which sqlite3 (isolation_level not None) opens a
    # transaction implicitly.
    _DML_KEYWORDS = frozenset(('INSERT', 'UPDATE', 'DELETE', 'REPLACE'))

    def execute(self, sql: str, parameters: Union[Tuple, Dict] = ()) -> 'Cursor':
        """
        Execute a single SQL statement.

        Args:
            sql: SQL statement to execute
            parameters: Parameters for SQL (sequence for ``?``, mapping for
                ``:name``)

        Returns:
            Self for chaining

        Raises:
            ProgrammingError: closed connection, wrong number of parameters
            OperationalError / IntegrityError / DataError: the engine
                rejected the statement
        """
        self._check_open()
        self._trace(sql)
        self._run(sql, parameters)
        return self

    def _check_open(self) -> None:
        if self.connection._closed:
            raise ProgrammingError("Cannot operate on a closed database.")

    @staticmethod
    def _trace(sql: str) -> None:
        if _trace_callback:
            try:
                _trace_callback(sql)
            except Exception:
                if _enable_callback_tracebacks_flag:
                    import traceback
                    traceback.print_exc()

    def _run(
        self,
        sql: str,
        parameters: Any = (),
        implicit_begin: bool = True,
    ) -> None:
        """Execute one statement and load its result into the cursor."""
        conn = self.connection
        user_sql = sql
        if conn._sqlite_types:
            sql = _sql.rewrite_ddl_types(sql)
        keyword = _sql.first_keyword(sql)

        # Bind parameters: natively ($1..$n) on the in-process binding, as
        # SQL literals for the REPL and wire transports.
        if conn._backend is not None:
            stmt, values = self._bind_native(sql, parameters)
        else:
            stmt, values = self._bind_parameters(sql, parameters), None

        # sqlite3.Cursor.lastrowid: when the user runs an INSERT we
        # transparently append RETURNING <pk> so the engine hands the new
        # row's PK back. lastrowid is cleared on every INSERT and left
        # untouched by other statements.
        # sqlite3 opens a transaction implicitly before DML unless the
        # connection is in autocommit mode (isolation_level=None).
        if (implicit_begin and conn.isolation_level is not None
                and not conn._in_transaction and keyword in self._DML_KEYWORDS):
            conn.begin()

        # SQLite schema semantics (sqlite_types=True): INTEGER PRIMARY KEY
        # assignment and the declared-type catalog.
        rowid_plan: Optional[_RowidPlan] = None
        schema_state = None
        if conn._sqlite_types:
            if keyword in ('INSERT', 'REPLACE'):
                stmt, values, rowid_plan = conn._rowid_prepare_insert(stmt, values)
            elif keyword == 'UPDATE':
                rowid_plan = conn._rowid_prepare_update(stmt)
            elif keyword in ('CREATE', 'DROP', 'ALTER'):
                schema_state = conn._schema_before(user_sql, keyword)

        is_insert = bool(self._INSERT_RE.match(stmt))
        if is_insert and conn._sqlite_types:
            conn._rowid_sync_for(stmt)
        stmt, lastrowid_pk = self._maybe_inject_returning(stmt)

        # Result columns as sqlite3 names and types them, for SELECT.
        plan = conn._result_plan(stmt) if keyword in ('SELECT', 'WITH') else None
        run_stmt = stmt
        if plan is not None:
            fixed = plan.join_order_fix() if conn._sqlite_types else None
            if fixed is not None:
                run_stmt, plan.hidden = fixed
            elif plan.collides and conn._backend is not None:
                run_stmt = plan.unique_sql(alias_all=False) or stmt
        if conn._sqlite_types:
            run_stmt = _sql.rewrite_sqlite_aggregates(run_stmt)

        try:
            try:
                if values is not None:
                    results = conn._execute_bound(run_stmt, values, plan)
                else:
                    results = conn._execute_sql(run_stmt)
            except Error:
                if plan is None or run_stmt == stmt:
                    raise
                # A rewrite the engine rejects: run the statement as written.
                plan.hidden = 0
                run_stmt = _sql.rewrite_sqlite_aggregates(stmt) if conn._sqlite_types else stmt
                if values is not None:
                    results = conn._execute_bound(run_stmt, values, plan)
                else:
                    results = conn._execute_sql(run_stmt)
            if plan is not None and plan.hidden and isinstance(results, dict):
                cut = plan.hidden
                results['columns'] = list(results.get('columns') or [])[:-cut]
                results['rows'] = [list(r)[:-cut] for r in results.get('rows') or []]
                if results.get('types'):
                    results['types'] = list(results['types'])[:-cut]
                if results.get('decl_oids'):
                    results['decl_oids'] = list(results['decl_oids'])[:-cut]
            if (plan is not None and conn._backend is not None and isinstance(results, dict)
                    and len(results.get('columns') or ()) != plan.width):
                # The binding merged columns that share a name (it returns
                # rows as dicts): run again with a unique name on each.
                results = conn._rerun_unique(plan, values, results)
        except Error:
            raise
        except Exception as e:
            raise DatabaseError(f"Error executing SQL: {e}") from None

        conn._track_transaction(keyword, stmt)
        if rowid_plan is not None:
            conn._rowid_after(rowid_plan)
        if keyword in ('CREATE', 'DROP', 'ALTER'):
            conn._schema_after(schema_state)

        if isinstance(results, dict):
            # Query result. 'types' holds the column type OIDs from the
            # server's RowDescription (wire transport); 'decl_oids' marks a
            # typed result from the in-process binding.
            columns = results.get('columns', [])
            if plan is not None and len(columns) != plan.width:
                plan = None
            self._results = self._convert_rows(
                results.get('rows', []), columns, results.get('types'),
                results.get('decl_oids'), stmt, plan,
            )
            if plan is not None and conn._backend is None and 'vector' in plan.udts:
                _parse_vectors(self._results, plan.udts)
            self._result_index = 0
            names = plan.labels if plan is not None else columns
            self.description = [
                (self._column_name(col), None, None, None, None, None, None)
                for col in names
            ] if columns else None
            self.rowcount = -1 if keyword in self._QUERY_KEYWORDS else len(self._results)

            # If we injected RETURNING <pk>, capture the last row's value as
            # cursor.lastrowid and hide the synthesised result set.
            if lastrowid_pk:
                inserted = len(self._results)
                if self._results:
                    last_row = self._results[-1]
                    pk_idx = columns.index(lastrowid_pk) if lastrowid_pk in columns else 0
                    if pk_idx < len(last_row):
                        try:
                            conn._last_insert_rowid = int(last_row[pk_idx])
                        except (TypeError, ValueError):
                            pass
                self._results = []
                self._result_index = 0
                self.description = None
                self.rowcount = inserted
        else:
            # Command result (INSERT, UPDATE, DELETE, DDL, ...)
            self._results = []
            self._result_index = 0
            self.description = None
            self.rowcount = results if isinstance(results, int) else -1
        if keyword in self._DML_KEYWORDS and self.rowcount > 0:
            conn._total_changes += self.rowcount
        if is_insert and not lastrowid_pk and self.rowcount > 0:
            # A table without an INTEGER PRIMARY KEY: HeliosDB has no rowid
            # to report, so the last inserted rowid is unknown.
            conn._last_insert_rowid = None
        # sqlite3 sets lastrowid after every execute() to the connection's
        # last inserted rowid (sqlite3_last_insert_rowid).
        self.lastrowid = conn._last_insert_rowid

    def executemany(self, sql: str, seq_of_parameters: Any) -> 'Cursor':
        """
        Execute a DML statement once per parameter set.

        As in sqlite3: only DML is accepted, ``rowcount`` is the total
        number of modified rows, and ``lastrowid`` is left unchanged.
        """
        self._check_open()
        conn = self.connection
        if conn._sqlite_types:
            sql = _sql.rewrite_ddl_types(sql)
        keyword = _sql.first_keyword(sql)
        if keyword in self._QUERY_KEYWORDS or _sql.has_keyword(sql, 'RETURNING'):
            raise ProgrammingError("executemany() can only execute DML statements.")
        self._trace(sql)
        saved_lastrowid = self.lastrowid
        total = 0
        backend = conn._backend
        if backend is not None and keyword in self._DML_KEYWORDS:
            # One engine call per run of rows with the same statement text
            # (an INTEGER PRIMARY KEY given as None becomes DEFAULT, which
            # changes the text), in order, so keys are assigned as in SQLite.
            groups: List[List[Any]] = []   # [stmt, [values, ...], plan]
            update_plan = None
            for parameters in seq_of_parameters:
                stmt_i, values = self._bind_native(sql, parameters)
                plan = None
                if conn._sqlite_types and keyword in ('INSERT', 'REPLACE'):
                    stmt_i, values, plan = conn._rowid_prepare_insert(stmt_i, values)
                elif conn._sqlite_types and keyword == 'UPDATE':
                    if update_plan is None:
                        update_plan = conn._rowid_prepare_update(stmt_i) or False
                    plan = update_plan or None
                if groups and groups[-1][0] == stmt_i:
                    groups[-1][1].append(values)
                    if plan is not None:
                        groups[-1][2] = plan if groups[-1][2] is None else groups[-1][2].merge(plan)
                else:
                    groups.append([stmt_i, [values], plan])
            if groups:
                if conn.isolation_level is not None and not conn._in_transaction:
                    conn.begin()
                for stmt_i, batch, plan in groups:
                    if conn._sqlite_types and keyword in ('INSERT', 'REPLACE'):
                        conn._rowid_sync_for(stmt_i)
                    total += conn._execute_bound_many(stmt_i, batch)
                    if plan is not None:
                        conn._rowid_after(plan)
                conn._total_changes += max(total, 0)
        else:
            disabled = conn._lastrowid_disabled
            conn._lastrowid_disabled = True  # no RETURNING rewrite per row
            try:
                for parameters in seq_of_parameters:
                    self._run(sql, parameters)
                    if self.rowcount > 0:
                        total += self.rowcount
            finally:
                conn._lastrowid_disabled = disabled
        self._results = []
        self._result_index = 0
        self.description = None
        self.rowcount = total
        self.lastrowid = saved_lastrowid
        return self

    def executescript(self, sql_script: str) -> 'Cursor':
        """
        Execute several SQL statements separated by semicolons.

        As in sqlite3, a pending transaction is committed first and the
        script then runs as written (no implicit transactions).
        """
        self._check_open()
        conn = self.connection
        if conn._in_transaction:
            conn.commit()
        for statement in _sql.split_statements(sql_script):
            self._trace(statement)
            self._run(statement, (), implicit_begin=False)
        self._results = []
        self._result_index = 0
        self.description = None
        return self

    def fetchone(self) -> Optional[Union[Tuple, Row]]:
        """
        Fetch next row from results.

        Returns:
            Row tuple/Row object, or None if no more rows
        """
        if self._result_index >= len(self._results):
            return None

        row_data = self._results[self._result_index]
        self._result_index += 1

        # Apply row factory
        if self.row_factory:
            return self.row_factory(self, tuple(row_data))
        elif self.connection.row_factory:
            return self.connection.row_factory(self, tuple(row_data))
        else:
            return tuple(row_data)

    def fetchmany(self, size: Optional[int] = None) -> List[Union[Tuple, Row]]:
        """
        Fetch multiple rows from results.

        Args:
            size: Number of rows to fetch (default: arraysize)

        Returns:
            List of row tuples/Row objects
        """
        if size is None:
            size = self.arraysize

        rows = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)

        return rows

    def fetchall(self) -> List[Union[Tuple, Row]]:
        """
        Fetch all remaining rows from results.

        Returns:
            List of row tuples/Row objects
        """
        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def close(self) -> None:
        """Close the cursor."""
        self._results = []
        self.description = None
        self.rowcount = -1

    def setinputsizes(self, sizes: List[int]) -> None:
        """Set input sizes (no-op for compatibility)."""
        pass

    def setoutputsize(self, size: int, column: Optional[int] = None) -> None:
        """Set output size (no-op for compatibility)."""
        pass

    def __iter__(self) -> Iterator[Union[Tuple, Row]]:
        """Iterate over result rows."""
        return self

    def __next__(self) -> Union[Tuple, Row]:
        """Get next row (iterator protocol)."""
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def _column_name(self, name: str) -> str:
        """Column name for cursor.description. With PARSE_COLNAMES, sqlite3
        drops a trailing ``[type]`` (and the space before it)."""
        if (self.connection.detect_types or 0) & PARSE_COLNAMES and isinstance(name, str):
            pos = name.find('[')
            if pos >= 0:
                if pos > 0 and name[pos - 1] == ' ':
                    pos -= 1
                return name[:pos]
        return name

    def _lookup_converter(self, name: Any, type_oid: Optional[int],
                          decltype: Optional[str] = None) -> Optional[Callable]:
        """Converter registered with register_converter() for a column, the
        way sqlite3 picks one: a ``[type]`` in the column name first
        (PARSE_COLNAMES), then the column's declared type (PARSE_DECLTYPES):
        its first word, as written in CREATE TABLE (``decltype``, '' for a
        result column that is not a table column), or, for tables created
        without this layer, the names of the column's type OID."""
        detect = self.connection.detect_types or 0
        if detect & PARSE_COLNAMES and isinstance(name, str):
            start = name.find('[')
            end = name.find(']', start + 1) if start >= 0 else -1
            if end > start:
                converter = _converters.get(name[start + 1:end].upper())
                if converter is not None:
                    return converter
        if detect & PARSE_DECLTYPES and decltype is not None:
            word = re.split(r'[\s(]', decltype.strip(), 1)[0].upper()
            return _converters.get(word) if word else None
        if detect & PARSE_DECLTYPES and type_oid is not None:
            for type_name in _types.TYPE_NAMES.get(type_oid, ()):
                converter = _converters.get(type_name)
                if converter is not None:
                    return converter
        return None

    def _convert_rows(
        self,
        rows: List[Any],
        columns: List[str],
        type_oids: Optional[List[Optional[int]]],
        decl_oids: Optional[List[Optional[int]]] = None,
        sql: Optional[str] = None,
        plan: Optional['_ResultPlan'] = None,
    ) -> List[List[Any]]:
        """Turn transport rows into sqlite3-typed rows.

        * In-process binding (``decl_oids`` given): values are already the
          Python types sqlite3 returns; ``decl_oids`` are the declared column
          types, used only to pick PARSE_DECLTYPES converters.
        * Wire transport (``type_oids`` given): each value is converted by
          its column's type OID (see ``_types``).
        * REPL transport (neither): values are passed through as text.

        Converters from register_converter() take precedence when
        detect_types asks for them, and receive the value's bytes as in
        sqlite3. A text_factory other than ``str`` is applied to text values.
        """
        typed = decl_oids is not None
        ncols = max(len(columns), len(type_oids or ()), len(decl_oids or ()))
        source = decl_oids if typed else type_oids
        oids: List[Optional[int]] = [
            source[i] if source and i < len(source) else None
            for i in range(ncols)
        ]
        converters: List[Optional[Callable]] = [None] * ncols
        if self.connection.detect_types and _converters:
            decltypes: List[Optional[str]] = [None] * ncols
            if self.connection.detect_types & PARSE_DECLTYPES and plan is not None:
                found = self.connection._plan_decltypes(plan)
                decltypes = [found[i] if i < len(found) else None for i in range(ncols)]
            elif self.connection.detect_types & PARSE_DECLTYPES and sql:
                found = self.connection._result_decltypes(sql, columns)
                decltypes = [found[i] if i < len(found) else None for i in range(ncols)]
            names = plan.labels if plan is not None else columns
            converters = [
                self._lookup_converter(names[i] if i < len(names) else None, oids[i],
                                       decltypes[i])
                for i in range(ncols)
            ]
        text_factory = getattr(self.connection, 'text_factory', str)
        if text_factory is str:
            text_factory = None
        if typed:
            if not any(converters) and text_factory is None:
                return [list(row) for row in rows]
        elif not any(oids) and not any(converters):
            return [list(row) for row in rows]

        converted = []
        for row in rows:
            out = []
            for i, value in enumerate(row):
                oid = oids[i] if i < ncols else None
                converter = converters[i] if i < ncols else None
                if value is None:
                    out.append(None)
                elif converter is not None:
                    data = (_embedded.converter_input(value) if typed
                            else _types.converter_input(oid, value))
                    out.append(converter(data))
                else:
                    if not typed:
                        value = _types.convert_value(oid, value)
                    if text_factory is not None and isinstance(value, str):
                        value = text_factory(value.encode('utf-8'))
                    out.append(value)
            converted.append(out)
        return converted

    def _plan_bindings(self, sql: str, parameters: Any) -> Tuple[List[_sql.Placeholder], List[int], List[Any]]:
        """Match ``parameters`` to the statement's placeholders the way
        sqlite3 does. Returns (placeholders, 1-based value index for each
        placeholder, values)."""
        placeholders = _sql.find_placeholders(sql)
        if parameters is None:
            parameters = ()
        is_map = isinstance(parameters, Mapping)
        if not is_map and not isinstance(parameters, (list, tuple)):
            try:
                parameters = list(parameters)
            except TypeError:
                raise ProgrammingError("parameters are of unsupported type") from None
        if not placeholders:
            if not is_map and len(parameters):
                raise ProgrammingError(
                    "Incorrect number of bindings supplied. The current statement "
                    f"uses 0, and there are {len(parameters)} supplied.")
            return [], [], []

        natives = [p for p in placeholders if p.kind == 'native']
        if natives:
            if len(natives) != len(placeholders):
                raise ProgrammingError(
                    "Cannot mix $1-style parameters with ?, :name, @name or $name")
            if is_map:
                raise ProgrammingError("$1-style parameters need a sequence, not a mapping")
            needed = max(p.key for p in natives)
            if needed != len(parameters):
                raise ProgrammingError(
                    "Incorrect number of bindings supplied. The current statement "
                    f"uses {needed}, and there are {len(parameters)} supplied.")
            return placeholders, [p.key for p in placeholders], list(parameters)

        slots: List[int] = []
        if is_map:
            values: List[Any] = []
            index_of: Dict[str, int] = {}
            for n, p in enumerate(placeholders, 1):
                if p.kind != 'named':
                    raise ProgrammingError(
                        f"Binding {n} has no name, but you supplied a dictionary "
                        "(which has only names).")
                if p.key not in index_of:
                    if p.key not in parameters:
                        raise ProgrammingError(
                            f"You did not supply a value for binding parameter :{p.key}.")
                    values.append(parameters[p.key])
                    index_of[p.key] = len(values)
                slots.append(index_of[p.key])
            return placeholders, slots, values

        largest = 0
        named: Dict[str, int] = {}
        for p in placeholders:
            if p.kind == 'qmark':
                largest += 1
                idx = largest
            elif p.kind == 'numbered':
                idx = p.key
                largest = max(largest, idx)
            elif p.key in named:
                idx = named[p.key]
            else:
                largest += 1
                idx = named[p.key] = largest
            slots.append(idx)
        if largest != len(parameters):
            raise ProgrammingError(
                "Incorrect number of bindings supplied. The current statement "
                f"uses {largest}, and there are {len(parameters)} supplied.")
        return placeholders, slots, list(parameters)

    @staticmethod
    def _splice(sql: str, placeholders: List[_sql.Placeholder], texts: List[str]) -> str:
        out = []
        pos = 0
        for p, text in zip(placeholders, texts):
            out.append(sql[pos:p.start])
            out.append(text)
            pos = p.end
        out.append(sql[pos:])
        return ''.join(out)

    def _bind_native(self, sql: str, parameters: Any) -> Tuple[str, List[Any]]:
        """``?`` / ``:name`` placeholders -> ``$n``, with values adapted to
        the types the binding accepts."""
        placeholders, slots, values = self._plan_bindings(sql, parameters)
        if not placeholders:
            return sql, []
        adapted = [_adapt_native(v, i) for i, v in enumerate(values, 1)]
        if all(p.kind == 'native' for p in placeholders):
            return sql, adapted
        return self._splice(sql, placeholders, [f'${n}' for n in slots]), adapted

    def _bind_parameters(self, sql: str, parameters: Any) -> str:
        """Bind parameters as SQL literals (REPL and wire transports).

        Supports ``?``, ``?NNN``, ``:name``, ``@name`` and ``$name``
        placeholders; placeholders inside strings and comments are left
        alone.
        """
        placeholders, slots, values = self._plan_bindings(sql, parameters)
        if not placeholders:
            return sql
        texts = [self._format_value(values[n - 1], n) for n in slots]
        return self._splice(sql, placeholders, texts)

    def _format_value(self, value: Any, index: int = 1) -> str:
        """A Python parameter as a SQL literal (REPL and wire transports),
        following sqlite3's binding rules: registered adapters apply, bool
        binds as 1 / 0, ints must fit in 64 bits, NaN binds as NULL, and a
        type sqlite3 cannot bind raises the error sqlite3 raises."""
        adapter = _adapters.get(type(value))
        if adapter is not None:
            value = adapter(value)
        if value is None:
            return 'NULL'
        elif isinstance(value, bool):
            # sqlite3 binds bool as the integer 1 / 0 (HeliosDB accepts
            # 1 / 0 for BOOLEAN columns too).
            return '1' if value else '0'
        elif isinstance(value, int):
            return str(_embedded.check_int64(int(value)))
        elif isinstance(value, float):
            if value != value:
                return 'NULL'
            if value in (float('inf'), float('-inf')):
                return "'Infinity'::float8" if value > 0 else "'-Infinity'::float8"
            return repr(float(value))
        elif isinstance(value, str):
            return _sql_literal(value)
        elif isinstance(value, (bytes, bytearray, memoryview)):
            # PostgreSQL hex bytea literal. HeliosDB rejects SQLite's X'..'
            # blob literal ("HexStringLiteral not yet supported").
            return f"'\\x{bytes(value).hex()}'::bytea"
        elif isinstance(value, datetime):
            return _sql_literal(value.isoformat(' '))
        elif isinstance(value, (date, datetime_time)):
            return _sql_literal(value.isoformat())
        elif isinstance(value, Decimal):
            return _sql_literal(str(value))
        elif _is_vector(value):
            # HeliosDB VECTOR, as in embedded mode
            return _sql_literal('[' + ', '.join(repr(float(x)) for x in value) + ']')
        raise _unsupported_parameter(value, index)


def _unsupported_parameter(value: Any, index: int) -> Exception:
    """The exception sqlite3 raises for a parameter of a type it cannot
    bind: ProgrammingError on Python 3.11+, InterfaceError before."""
    if sys.version_info >= (3, 11):
        return ProgrammingError(
            f"Error binding parameter {index}: type '{type(value).__name__}' is not supported")
    return InterfaceError(f"Error binding parameter {index - 1} - probably unsupported type.")


def _parse_vectors(rows: List[List[Any]], udts: List[Optional[str]]) -> None:
    """Daemon mode: the server sends VECTOR values as text ('[1.0,2.5]',
    type OID 25); return them as lists of floats, as embedded mode does."""
    cols = [i for i, udt in enumerate(udts) if udt == 'vector']
    for row in rows:
        for i in cols:
            value = row[i] if i < len(row) else None
            if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
                inner = value[1:-1].strip()
                try:
                    row[i] = [float(x) for x in inner.split(',')] if inner else []
                except ValueError:
                    pass


def _is_vector(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)


def _sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class _RowidPlan:
    """Work left after an INSERT/UPDATE that set an INTEGER PRIMARY KEY
    explicitly: move the table's rowid sequence past the largest key."""

    __slots__ = ('table', 'column', 'sequence', 'max_known', 'unknown')

    def __init__(self, table: str, column: str, sequence: str,
                 max_known: Optional[int], unknown: bool):
        self.table = table
        self.column = column
        self.sequence = sequence
        self.max_known = max_known
        self.unknown = unknown

    def merge(self, other: Optional['_RowidPlan']) -> '_RowidPlan':
        if other is not None:
            if other.max_known is not None:
                self.max_known = (other.max_known if self.max_known is None
                                  else max(self.max_known, other.max_known))
            self.unknown = self.unknown or other.unknown
        return self


_ROWID_DEFAULT_RE = re.compile(
    r"^\s*nextval\(\s*'([a-z0-9_]+" + re.escape(_sql.ROWID_SEQUENCE_SUFFIX) + r")'")
_INT_LITERAL_RE = re.compile(r'^[-+]?\d+$')
_NATIVE_PARAM_RE = re.compile(r'^\$(\d+)$')
_UPDATE_HEAD_RE = re.compile(
    r'\s*UPDATE\s+(?:OR\s+\w+\s+)?(?P<n1>"[^"]+"|[\w$]+)(?:\s*\.\s*(?P<n2>"[^"]+"|[\w$]+))?'
    r'(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?\s+SET\b', re.IGNORECASE)
_SET_END_RE = re.compile(r'\b(?:WHERE|RETURNING|FROM|ORDER|LIMIT)\b', re.IGNORECASE)


def _adapt_native(value: Any, index: int) -> Any:
    """A Python parameter -> a value the in-process binding binds, following
    sqlite3's rules (bool binds as an integer, ints must fit in 64 bits,
    registered adapters apply)."""
    kind = type(value)
    if kind is float and value != value:
        return None  # sqlite3 binds NaN as NULL
    if value is None or kind is str or kind is bytes or kind is float:
        return value
    if kind is int:
        return _embedded.check_int64(value)
    adapter = _adapters.get(kind)
    if adapter is not None:
        value = adapter(value)
        if value is None:
            return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return _embedded.check_int64(int(value))
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, datetime):
        return value.isoformat(' ')
    if isinstance(value, (date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if _is_vector(value):
        return [float(x) for x in value]  # HeliosDB VECTOR
    raise _unsupported_parameter(value, index)


# ============================================================================
# CONNECTION CLASS - Main database interface
# ============================================================================

class _ResultPlan:
    """sqlite3's view of a SELECT's result columns (see _sql.parse_select):
    the name sqlite3 reports for each column, the table column it comes
    from (for its declared type), and how to give every column a unique
    name when the in-process binding would merge columns that share one."""

    __slots__ = ('sql', 'shape', 'labels', 'sources', 'kinds', 'items', 'keys',
                 'qualifiers', 'udts', 'width', 'collides', 'hidden')

    UNIQUE_PREFIX = '_hc'

    def __init__(self, sql: str, shape: '_sql.SelectShape'):
        self.sql = sql
        self.shape = shape
        self.labels: List[str] = []
        self.sources: List[Optional[Tuple[str, str]]] = []
        self.kinds: List[str] = []          # 'column' | 'expr'
        self.items: List[int] = []          # select-list item of each column
        self.keys: List[Optional[str]] = [] # name the engine gives it, when known
        self.qualifiers: List[Optional[str]] = []
        self.udts: List[Optional[str]] = []  # source column's udt_name
        self.width = 0
        self.collides = False
        # trailing columns added only to sort by (see join_order_fix)
        self.hidden = 0

    def add(self, label: str, source: Optional[Tuple[str, str]], kind: str, item: int,
            key: Optional[str], qualifier: Optional[str] = None,
            udt: Optional[str] = None) -> None:
        self.labels.append(label)
        self.sources.append(source)
        self.kinds.append(kind)
        self.items.append(item)
        self.keys.append(key)
        self.qualifiers.append(qualifier)
        self.udts.append(udt)
        self.width += 1

    def finish(self) -> None:
        seen = set()
        for key in self.keys:
            if key is None:
                continue
            folded = key.lower()
            if folded in seen:
                self.collides = True
            seen.add(folded)

    def expression_columns(self) -> List[bool]:
        return [kind == 'expr' for kind in self.kinds]

    def unique_sql(self, alias_all: bool, force: Tuple[int, ...] = (),
                   extra_edits: Tuple[Tuple[int, int, str], ...] = ()) -> Optional[str]:
        """The statement with a unique alias (``"_hcN"``, N = result
        position) on every column whose name collides with another's, and
        with ``alias_all`` also on every expression without an alias (and
        on the columns in ``force``). A ``*`` with colliding columns is
        spelled out. ``extra_edits`` are further (start, end, text)
        replacements outside the select list. None when nothing needs
        renaming."""
        counts: Dict[str, int] = {}
        for key in self.keys:
            if key is not None:
                counts[key.lower()] = counts.get(key.lower(), 0) + 1

        def needs(col: int) -> bool:
            if col in force:
                return True
            key = self.keys[col]
            if key is None:
                return alias_all or self.kinds[col] == 'expr'
            return counts.get(key.lower(), 0) > 1

        by_item: Dict[int, List[int]] = {}
        for col, item in enumerate(self.items):
            by_item.setdefault(item, []).append(col)
        edits = []
        for index, item in enumerate(self.shape.items):
            cols = by_item.get(index, [])
            if not any(needs(c) for c in cols):
                continue
            if item.kind == 'star':
                parts = []
                for c in cols:
                    ref = (f'{_sql_ident(self.qualifiers[c])}.{_sql_ident(self.sources[c][1])}'
                           if self.qualifiers[c] is not None and self.sources[c] is not None
                           else None)
                    if ref is None:
                        return None
                    parts.append(f'{ref} AS "{self.UNIQUE_PREFIX}{c}"' if needs(c) else ref)
                edits.append((item.start, item.end, ', '.join(parts)))
            else:
                c = cols[0]
                expr = self.sql[item.expr_start:item.expr_end]
                edits.append((item.start, item.end, f'{expr} AS "{self.UNIQUE_PREFIX}{c}"'))
        edits.extend(extra_edits)
        if not edits:
            return None
        out = self.sql
        for start, end, text in sorted(edits, reverse=True):
            out = out[:start] + text + out[end:]
        return out

    def join_order_fix(self) -> Optional[Tuple[str, int]]:
        """HeliosDB Nano (4.31 binding, 4.41 server) ignores ``ORDER BY
        t.col`` and ``ORDER BY <n>`` when the query joins tables; ordering by
        a select-list alias works. Alias the select-list columns those terms
        name, add the ones not selected as trailing columns, and order by
        the aliases. Returns (statement, number of trailing columns to drop
        from the result), or None when not applicable."""
        shape = self.shape
        if len(shape.tables) < 2 or shape.compound:
            return None
        terms = _sql.parse_order_by(self.sql)
        if not terms or not any(t.kind in ('column', 'position') and
                                (t.kind == 'position' or t.qualifier is not None)
                                for t in terms):
            return None
        force = []
        edits = []
        hidden = []
        for term in terms:
            col = None
            if term.kind == 'column' and term.qualifier is None:
                continue        # unqualified names sort correctly
            if term.kind == 'position':
                if 1 <= (term.position or 0) <= self.width:
                    col = term.position - 1
            elif term.kind == 'column' and term.qualifier is not None:
                for c in range(self.width):
                    source = self.sources[c]
                    item = shape.items[self.items[c]]
                    if source is None or source[1] != term.column:
                        continue
                    alias = item.qualifier if item.kind == 'column' else self.qualifiers[c]
                    table = [t for t in shape.tables if t.alias == term.qualifier]
                    if alias == term.qualifier or (alias is None and table
                                                   and table[0].name == source[0]):
                        col = c
                        break
            else:
                continue
            if col is None:
                if term.kind != 'column' or shape.distinct:
                    return None
                # not selected: sort by a trailing column dropped afterwards
                name = f'_ho{len(hidden)}'
                hidden.append(f'{self.sql[term.start:term.end]} AS "{name}"')
                edits.append((term.start, term.end, f'"{name}"'))
                continue
            force.append(col)
            edits.append((term.start, term.end, f'"{self.UNIQUE_PREFIX}{col}"'))
        if not edits:
            return None
        if hidden:
            edits.append((shape.list_end, shape.list_end, ', ' + ', '.join(hidden)))
        out = self.unique_sql(alias_all=False, force=tuple(force), extra_edits=tuple(edits))
        return (out, len(hidden)) if out is not None else None


class Connection:
    """
    Database connection object.
    Fully compatible with sqlite3.Connection API.
    """

    def __init__(
        self,
        database: str,
        timeout: float = 5.0,
        detect_types: int = 0,
        isolation_level: Optional[str] = "DEFERRED",
        check_same_thread: bool = True,
        factory: Optional[type] = None,
        cached_statements: int = 128,
        uri: bool = False,
        **kwargs
    ):
        """
        Initialize database connection.

        Args:
            database: Path to database file, or ':memory:' for in-memory
            timeout: Connection timeout in seconds
            detect_types: Type detection flags (PARSE_DECLTYPES | PARSE_COLNAMES)
            isolation_level: Transaction isolation level (DEFERRED, IMMEDIATE, EXCLUSIVE, None)
            check_same_thread: Enforce single-threaded access
            factory: Custom Row factory
            cached_statements: Number of statements to cache
            uri: Treat database as URI
            **kwargs: Additional HeliosDB-specific parameters
        """
        self.database = database
        self.timeout = timeout
        self.detect_types = detect_types
        self.isolation_level = isolation_level
        self.check_same_thread = check_same_thread
        self.row_factory = factory
        self.text_factory: Callable = str
        self._cached_statements = cached_statements
        # In-process engine (heliosdb-nano-embedded); None for the REPL and
        # daemon transports.
        self._backend: Optional[_embedded.EmbeddedBackend] = None
        self._heliosdb_process: Any = None

        self._closed = False
        self._in_transaction = False
        self._thread_id = threading.get_ident() if check_same_thread else None

        # cursor.lastrowid support: cache the int-PK column name per table
        # so we don't issue PRAGMA table_info on every INSERT. Pass
        # `lastrowid=False` (or `lastrowid_disabled=True`) to connect() to
        # opt out of the auto-RETURNING rewrite.
        self._lastrowid_pk_cache: Dict[str, Optional[str]] = {}
        self._lastrowid_disabled: bool = bool(
            kwargs.get('lastrowid_disabled', False)
            or kwargs.get('lastrowid', True) is False
        )

        # HeliosDB-specific configuration
        self._mode = kwargs.get('mode', 'embedded')  # embedded, daemon, hybrid
        self._data_dir = kwargs.get('data_dir', None)
        self._server_port = kwargs.get('server_port', 5432)
        self._server_host = kwargs.get('server_host', '127.0.0.1')

        # Daemon mode (PostgreSQL wire protocol). `dsn` is a libpq connection
        # string or postgresql:// URI; the server_* keywords override its
        # fields. Without a password, libpq's PGPASSWORD / ~/.pgpass apply.
        self._dsn: Optional[str] = kwargs.get('dsn')
        self._server_user: str = kwargs.get('server_user', 'helios')
        self._server_password: Optional[str] = kwargs.get('server_password')
        self._server_database: str = kwargs.get('server_database', 'heliosdb')
        self._server_explicit: Dict[str, Any] = {
            key: kwargs[key]
            for key in ('server_host', 'server_port', 'server_user',
                        'server_password', 'server_database')
            if key in kwargs
        }
        # One wire session per Connection, so BEGIN/COMMIT and the
        # statements between them share a transaction.
        self._pg_conn: Any = None

        # Map SQLite column types in CREATE/ALTER TABLE to HeliosDB types with
        # the same meaning (64-bit INTEGER, 8-byte REAL, BLOB -> BYTEA, ...;
        # see _sql.rewrite_ddl_types). sqlite_types=False sends DDL verbatim.
        self._sqlite_types: bool = bool(kwargs.get('sqlite_types', True))
        # Record each column's declared type (as written in CREATE TABLE)
        # in the heliosdb_sqlite_decltypes table, so PARSE_DECLTYPES finds
        # converters by the declared name (INTEGER, DATETIME, POINT, ...)
        # like sqlite3_column_decltype(). declared_types=False turns it off.
        self._declared_types: bool = self._sqlite_types and bool(kwargs.get('declared_types', True))
        self._catalog_exists: Optional[bool] = None
        self._decltype_cache: Dict[str, Dict[str, str]] = {}
        # INTEGER PRIMARY KEY tables: (column, sequence, ordinal) per table,
        # and the value each sequence is known to be at or past.
        self._rowid_cache: Dict[str, Optional[Tuple[str, str, int]]] = {}
        self._rowid_hw: Dict[str, int] = {}
        self._insert_parse_cache: Dict[str, Optional[_sql.InsertInfo]] = {}
        self._total_changes = 0
        # sqlite3_last_insert_rowid(): 0 until a row is inserted; None after
        # an insert into a table without an INTEGER PRIMARY KEY.
        self._last_insert_rowid: Optional[int] = 0
        # SELECT shapes by statement text, and table layouts for them.
        self._shape_cache: Dict[str, Optional[_sql.SelectShape]] = {}
        self._layout_cache: Dict[str, Optional[List[Tuple[str, str, str]]]] = {}
        self._catalog_labels: Optional[bool] = None
        # Daemon mode: wrap each statement inside a transaction in a
        # savepoint so a failing statement leaves the transaction usable,
        # as in SQLite. statement_savepoints=False saves the round trips.
        self._statement_savepoints: bool = bool(kwargs.get('statement_savepoints', True))

        # embedded mode: 'auto' (in-process binding when installed, else the
        # REPL subprocess with a warning), 'binding' (require the binding)
        # or 'repl' (always the subprocess).
        self._embedded_backend: str = str(
            kwargs.get('embedded_backend')
            or os.environ.get('HELIOSDB_SQLITE_BACKEND')
            or 'auto'
        ).lower()

        # Initialize HeliosDB connection based on mode. As in sqlite3, no
        # transaction is open yet: one starts implicitly before the first
        # INSERT/UPDATE/DELETE/REPLACE unless isolation_level is None.
        self._initialize_heliosdb()

    @property
    def in_transaction(self) -> bool:
        """True while a transaction is open (sqlite3.Connection.in_transaction)."""
        return self._in_transaction

    def _embedded_data_dir(self) -> str:
        """The engine's data directory for ``database``.

        Each database path is its own database, as in sqlite3: the path
        itself is used as the engine's data directory (``app.db/``).
        ``data_dir=`` overrides it. Versions before 3.1.0 stored every
        database of a directory in one shared ``heliosdb-data`` directory
        next to it; when that exists and the path does not, it is still
        used (with a warning) so existing data stays reachable."""
        if self._data_dir:
            return str(self._data_dir)
        path = Path(self.database)
        legacy = path.parent / 'heliosdb-data'
        if not path.exists() and legacy.is_dir():
            warnings.warn(
                f"heliosdb_sqlite: using the shared data directory {str(legacy)!r} "
                f"created by an earlier version, which every database in "
                f"{str(path.parent)!r} shares. Move it to {str(path)!r} (or pass "
                f"data_dir=) to give this database its own storage.",
                RuntimeWarning, stacklevel=4)
            return str(legacy)
        if path.is_file():
            raise OperationalError(
                f"unable to open database: {str(path)!r} is a file (an SQLite "
                "database?); HeliosDB keeps a database in a directory. Pass "
                "data_dir= or a new path.")
        return str(path)

    def _check_thread(self) -> None:
        """Verify we're on the same thread (if check_same_thread=True)."""
        if self.check_same_thread and self._thread_id != threading.get_ident():
            raise ProgrammingError(
                "SQLite objects created in a thread can only be used in that same thread. "
                "The object was created in thread id {} and this is thread id {}.".format(
                    self._thread_id, threading.get_ident()
                )
            )

    def _initialize_heliosdb(self) -> None:
        """Initialize HeliosDB connection based on mode."""
        if self._mode == 'embedded':
            self._init_embedded_mode()
        elif self._mode == 'daemon':
            self._init_daemon_mode()
        elif self._mode == 'hybrid':
            self._init_hybrid_mode()
        else:
            raise InterfaceError(f"Unknown mode: {self._mode}")

    def _init_embedded_mode(self) -> None:
        """Initialize embedded mode: the in-process binding when available,
        otherwise the persistent REPL subprocess."""
        choice = self._embedded_backend
        if choice not in ('auto', 'binding', 'repl'):
            raise InterfaceError(
                f"Unknown embedded_backend {choice!r}; use 'auto', 'binding' or 'repl'")
        if choice != 'repl':
            module = _embedded.load_binding()
            if module is not None:
                path = None if self.database == ':memory:' else self._embedded_data_dir()
                try:
                    self._backend = _embedded.EmbeddedBackend(module, path, self.timeout)
                except Exception as e:
                    raise OperationalError(f"unable to open database: {e}") from None
                return
            if choice == 'binding':
                raise InterfaceError(
                    "embedded_backend='binding' needs the heliosdb-nano-embedded package "
                    "(pip install heliosdb-nano-embedded)")
            warnings.warn(
                "heliosdb-nano-embedded is not installed, so embedded mode drives the "
                "'heliosdb-nano repl' subprocess, which returns every value as text "
                "(for example '7' instead of 7). Install it for sqlite3-typed results: "
                "pip install heliosdb-nano-embedded",
                RuntimeWarning,
                stacklevel=5,
            )

        # Determine data directory
        if self.database == ':memory:':
            self._heliosdb_args = ['--memory']
        else:
            # Use database path as data directory
            self._heliosdb_args = ['--data-dir', self._embedded_data_dir()]

        # Start persistent REPL process for state preservation
        self._start_persistent_repl()

    def _start_persistent_repl(self) -> None:
        """Start a persistent REPL process for embedded mode."""
        import subprocess
        import os
        import fcntl
        cmd = [_resolve_binary(), 'repl'] + self._heliosdb_args

        try:
            self._heliosdb_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # Unbuffered
            )
            # Make stdout non-blocking
            fd = self._heliosdb_process.stdout.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

            # Read and discard the startup banner
            self._read_until_prompt()
        except Exception as e:
            self._heliosdb_process = None
            raise InterfaceError(f"Failed to start HeliosDB REPL: {e}")

    def _read_until_prompt(self) -> str:
        """Read output until we see initialization complete."""
        import os
        output = b''
        start_time = time.time()

        while time.time() - start_time < 5:  # 5 second timeout for startup
            try:
                chunk = os.read(self._heliosdb_process.stdout.fileno(), 4096)
                if chunk:
                    output += chunk
                    # Check if we've seen the command prompt hints
                    if b'Commands:' in output or b'\\q quit' in output:
                        break
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except:
                break

        return output.decode('utf-8', errors='replace')

    def _init_daemon_mode(self) -> None:
        """Initialize server daemon mode: open the wire session now, so bad
        credentials or an unreachable server fail in connect()."""
        self._heliosdb_process = None
        self._daemon_connection()

    def _init_hybrid_mode(self) -> None:
        """Initialize hybrid mode (embedded + optional server)."""
        self._init_embedded_mode()
        # Server can be started on-demand via switch_to_server()

    def _execute_sql(self, sql: str) -> Union[Dict, int]:
        """
        Execute SQL through HeliosDB.

        Args:
            sql: SQL statement to execute

        Returns:
            Dict with results for queries, int for row count

        Raises:
            DatabaseError: If execution fails
        """
        if self._backend is not None:
            return self._execute_bound(sql, [])
        if self._mode == 'embedded' or self._mode == 'hybrid':
            return self._execute_embedded(sql)
        elif self._mode == 'daemon':
            return self._execute_daemon(sql)

    def _execute_bound(self, sql: str, values: List[Any],
                       plan: Optional['_ResultPlan'] = None) -> Union[Dict, int]:
        """Execute one statement with ``$n`` parameters on the in-process
        binding, mapping engine errors to sqlite3's exception classes."""
        try:
            return self._backend.run(sql, values, plan)
        except _embedded._LockTimeout:
            raise OperationalError("database is locked") from None
        except Error:
            raise
        except (OverflowError, ProgrammingError):
            raise
        except Exception as e:
            message = str(e).strip()
            raise _DRIVER_ERRORS[_embedded.classify_error(message)](message) from None

    def _rerun_unique(self, plan: _ResultPlan, values: Optional[List[Any]],
                      first: Dict[str, Any]) -> Dict[str, Any]:
        """Run a SELECT whose result lost columns to a name collision again,
        with a unique name on each column (see _ResultPlan.unique_sql)."""
        alt = plan.unique_sql(alias_all=True)
        if alt is not None:
            if self._sqlite_types:
                alt = _sql.rewrite_sqlite_aggregates(alt)
            try:
                result = self._execute_bound(alt, values or [], plan)
            except Error:
                result = None
            if isinstance(result, dict) and len(result.get('columns') or ()) == plan.width:
                return result
        warnings.warn(
            "heliosdb_sqlite: result columns with the same name were merged by the "
            "embedded engine binding; give each column a distinct alias "
            f"(expected {plan.width} columns, got {len(first.get('columns') or ())})",
            RuntimeWarning, stacklevel=4)
        return first

    def _execute_bound_many(self, sql: str, batch: List[List[Any]]) -> int:
        try:
            return self._backend.run_many(sql, batch)
        except _embedded._LockTimeout:
            raise OperationalError("database is locked") from None
        except Error:
            raise
        except Exception as e:
            message = str(e).strip()
            raise _DRIVER_ERRORS[_embedded.classify_error(message)](message) from None

    def _track_transaction(self, keyword: str, sql: str) -> None:
        """Follow BEGIN / COMMIT / ROLLBACK the application runs itself."""
        if keyword in ('BEGIN', 'START'):
            self._in_transaction = True
        elif keyword in ('COMMIT', 'END'):
            self._in_transaction = False
        elif keyword == 'ROLLBACK' and not _sql.has_keyword(sql, 'TO'):
            self._in_transaction = False

    # ------------------------------------------------------------------
    # SQLite schema semantics: rowid assignment and declared column types
    # ------------------------------------------------------------------

    DECLTYPE_CATALOG = 'heliosdb_sqlite_decltypes'

    @property
    def total_changes(self) -> int:
        """Rows inserted, updated or deleted through this Connection since
        it was opened (sqlite3.Connection.total_changes)."""
        return self._total_changes

    @property
    def _schema_bookkeeping(self) -> bool:
        """Rowid sequence upkeep and the declared-type catalog need typed
        catalog queries: the in-process binding or daemon mode, not the
        text REPL fallback."""
        return self._sqlite_types and (self._backend is not None or self._mode == 'daemon')

    def _internal_rows(self, sql: str) -> List[List[Any]]:
        """Run an internal statement (catalog lookups, sequence upkeep)
        outside any Cursor; returns its rows."""
        result = self._execute_sql(sql)
        if isinstance(result, dict):
            return [list(r) for r in result.get('rows', [])]
        return []

    def _invalidate_schema_caches(self) -> None:
        self._rowid_cache.clear()
        self._rowid_hw.clear()
        self._decltype_cache.clear()
        self._insert_parse_cache.clear()
        self._lastrowid_pk_cache.clear()
        self._layout_cache.clear()

    def _table_exists(self, table: str) -> bool:
        rows = self._internal_rows(
            'SELECT table_name FROM information_schema.tables '
            f'WHERE table_name = {_sql_literal(table)}')
        return bool(rows)

    def _rowid_info(self, table: str) -> Optional[Tuple[str, str, int]]:
        """``(column, sequence, 0-based position)`` of ``table``'s INTEGER
        PRIMARY KEY (a column whose default draws from a ``*_rowid_seq``
        sequence created by this layer), or None."""
        if table in self._rowid_cache:
            return self._rowid_cache[table]
        if not self._schema_bookkeeping:
            return None
        info = None
        try:
            rows = self._internal_rows(
                'SELECT column_name, column_default, ordinal_position FROM '
                f'information_schema.columns WHERE table_name = {_sql_literal(table)}')
            rows = [r for r in rows if len(r) >= 3]
            rows.sort(key=lambda r: int(r[2] or 0))
            for position, row in enumerate(rows):
                m = _ROWID_DEFAULT_RE.match(str(row[1] or ''))
                if m:
                    info = (str(row[0]), m.group(1), position)
                    break
        except (Error, TypeError, ValueError):
            info = None
        if len(self._rowid_cache) > 256:
            self._rowid_cache.clear()
        self._rowid_cache[table] = info
        return info

    def _rowid_prepare_insert(self, stmt: str, values: Optional[List[Any]]
                              ) -> Tuple[str, Optional[List[Any]], Optional[_RowidPlan]]:
        """SQLite assigns an INTEGER PRIMARY KEY when an INSERT gives NULL
        for it: turn such NULLs into DEFAULT, and note explicit keys so the
        sequence can be moved past them afterwards."""
        if stmt in self._insert_parse_cache:
            info = self._insert_parse_cache[stmt]
        else:
            info = _sql.parse_insert(stmt)
            if len(self._insert_parse_cache) > 64:
                self._insert_parse_cache.clear()
            self._insert_parse_cache[stmt] = info
        if info is None or info.default_values:
            return stmt, values, None
        rowid = self._rowid_info(info.table)
        if rowid is None:
            return stmt, values, None
        column, sequence, position = rowid
        if info.columns is not None:
            if column in info.columns:
                position = info.columns.index(column)
            else:
                return stmt, values, None  # key omitted: assigned by the sequence
        if info.rows is None:
            return stmt, values, _RowidPlan(info.table, column, sequence, None, True)
        known: List[int] = []
        unknown = False
        to_default: List[Tuple[int, int]] = []
        for row in info.rows:
            if position >= len(row):
                unknown = True
                continue
            start, end = row[position]
            expr = stmt[start:end].strip()
            upper = expr.upper()
            if upper == 'DEFAULT':
                continue
            if upper == 'NULL':
                to_default.append((start, end))
                continue
            m = _NATIVE_PARAM_RE.match(expr)
            if m and values is not None and 0 < int(m.group(1)) <= len(values):
                value = values[int(m.group(1)) - 1]
                if value is None:
                    to_default.append((start, end))
                elif isinstance(value, int):
                    known.append(int(value))
                else:
                    unknown = True
                continue
            if _INT_LITERAL_RE.match(expr):
                known.append(int(expr))
            else:
                unknown = True
        for start, end in reversed(to_default):
            stmt = stmt[:start] + ' DEFAULT' + stmt[end:]
        if not known and not unknown:
            return stmt, values, None
        return stmt, values, _RowidPlan(info.table, column, sequence,
                                        max(known) if known else None, unknown)

    def _rowid_prepare_update(self, stmt: str) -> Optional[_RowidPlan]:
        """An UPDATE that assigns an INTEGER PRIMARY KEY may move it past
        the sequence; note it so the sequence follows."""
        m = _UPDATE_HEAD_RE.match(stmt)
        if not m:
            return None
        table = _sql.identifier_name(m.group('n2') or m.group('n1'))
        rowid = self._rowid_info(table)
        if rowid is None:
            return None
        column, sequence, _ = rowid
        start = m.end()
        end = len(stmt)
        for s0, e0 in _sql.code_spans(stmt):
            if e0 <= start:
                continue
            found = _SET_END_RE.search(stmt, max(s0, start), e0)
            if found:
                end = found.start()
                break
        bounds = [start - 1] + _sql._top_level_commas(stmt, start, end) + [end]
        for a, b in zip(bounds, bounds[1:]):
            lhs = stmt[a + 1:b].split('=', 1)[0].strip()
            if lhs and _sql.identifier_name(lhs.split('.')[-1]) == column:
                return _RowidPlan(table, column, sequence, None, True)
        return None

    def _rowid_sync_for(self, stmt: str) -> None:
        """Embedded mode: before an INSERT into an INTEGER PRIMARY KEY
        table, make sure the binding's sequence state is this database's
        (see _embedded._SEQUENCE_OWNERS)."""
        backend = self._backend
        if backend is None or not self._schema_bookkeeping:
            return
        info = self._insert_parse_cache.get(stmt)
        if info is None:
            info = _sql.parse_insert(stmt)
        if info is None:
            return
        rowid = self._rowid_info(info.table)
        if rowid is None:
            return
        column, sequence, _ = rowid
        if backend.claim_sequence(sequence):
            self._rowid_resync(info.table, column, sequence)

    def _rowid_resync(self, table: str, column: str, sequence: str) -> None:
        """Set the rowid sequence so its next value is max(key) + 1."""
        try:
            self._internal_rows(
                f'SELECT setval({_sql_literal(sequence)}, '
                f'COALESCE(max({_sql_ident(column)}), 0) + 1, false) FROM {_sql_ident(table)}')
            self._rowid_hw.pop(table, None)
        except Error as e:
            warnings.warn(
                f"heliosdb_sqlite: could not resynchronise the INTEGER PRIMARY KEY sequence of "
                f"{table!r}: {e}", RuntimeWarning)

    def _rowid_after(self, plan: _RowidPlan) -> None:
        """Move the rowid sequence past the largest explicit key, so the
        next INSERT that omits the key gets max + 1 as in SQLite."""
        if self._backend is not None and self._backend.claim_sequence(plan.sequence):
            self._rowid_resync(plan.table, plan.column, plan.sequence)
            return
        try:
            top = plan.max_known
            if plan.unknown:
                rows = self._internal_rows(
                    f'SELECT max({_sql_ident(plan.column)}) FROM {_sql_ident(plan.table)}')
                if rows and rows[0][0] is not None:
                    found = int(rows[0][0])
                    top = found if top is None else max(top, found)
            if top is None or top < 1 or top <= self._rowid_hw.get(plan.table, 0):
                return
            seq = _sql_literal(plan.sequence)
            rows = self._internal_rows(
                f'SELECT setval({seq}, GREATEST(nextval({seq}) - 1, {int(top)}))')
            self._rowid_hw[plan.table] = int(rows[0][0]) if rows and rows[0][0] is not None else top
        except (Error, TypeError, ValueError):
            # The row is stored; only automatic keys after it are affected.
            warnings.warn(
                f"heliosdb_sqlite: could not advance the INTEGER PRIMARY KEY sequence of "
                f"{plan.table!r}; a later INSERT without a key may collide", RuntimeWarning)

    def _schema_before(self, user_sql: str, keyword: str) -> Optional[Tuple[str, Any]]:
        """Bookkeeping before a CREATE / DROP / ALTER TABLE runs."""
        if not self._schema_bookkeeping:
            return None
        try:
            if keyword == 'CREATE':
                create = _sql.parse_create_table(user_sql)
                if create is None:
                    return None
                existed = self._table_exists(create.table)
                if not existed and create.rowid_column is not None:
                    # A new table starts its keys at 1, as in SQLite, even
                    # if an older table of the same name left a sequence.
                    seq = _sql.rowid_sequence_name(create.table, create.rowid_column)
                    self._internal_rows(f'DROP SEQUENCE IF EXISTS {seq}')
                    self._internal_rows(f'CREATE SEQUENCE {seq}')
                return ('create', (create, existed))
            if keyword == 'DROP':
                tables = _sql.dropped_tables(user_sql)
                if not tables:
                    return None
                return ('drop', [(t, self._rowid_info(t)) for t in tables])
            if keyword == 'ALTER':
                alter = _sql.parse_alter_table(user_sql)
                return ('alter', alter) if alter is not None else None
        except Error:
            return None
        return None

    def _schema_after(self, state: Optional[Tuple[str, Any]]) -> None:
        """Bookkeeping after a CREATE / DROP / ALTER TABLE succeeded."""
        self._invalidate_schema_caches()
        if state is None:
            return
        kind, data = state
        try:
            if kind == 'create':
                create, existed = data
                if not existed and self._declared_types and create.columns:
                    self._catalog_write(create.table, create.columns)
            elif kind == 'drop':
                for table, rowid in data:
                    if rowid is not None:
                        self._internal_rows(f'DROP SEQUENCE IF EXISTS {rowid[1]}')
                    if self._declared_types and self._catalog_ready():
                        self._internal_rows(
                            f'DELETE FROM {self.DECLTYPE_CATALOG} '
                            f'WHERE table_name = {_sql_literal(table)}')
            elif kind == 'alter' and self._declared_types:
                table, action, args = data
                if action == 'add_column':
                    self._catalog_write(table, [args], replace=False)
                elif not self._catalog_ready():
                    return
                elif action == 'rename_table':
                    self._internal_rows(
                        f'UPDATE {self.DECLTYPE_CATALOG} SET table_name = {_sql_literal(args[0])} '
                        f'WHERE table_name = {_sql_literal(table)}')
                elif action == 'rename_column':
                    label = args[2] if len(args) > 2 and args[2] != args[1] else None
                    set_label = ''
                    if self._catalog_has_labels(add=True):
                        set_label = (', column_label = '
                                     + ('NULL' if label is None else _sql_literal(label)))
                    self._internal_rows(
                        f'UPDATE {self.DECLTYPE_CATALOG} SET column_name = {_sql_literal(args[1])}'
                        f'{set_label} WHERE table_name = {_sql_literal(table)} '
                        f'AND column_name = {_sql_literal(args[0])}')
                elif action == 'drop_column':
                    self._internal_rows(
                        f'DELETE FROM {self.DECLTYPE_CATALOG} WHERE table_name = {_sql_literal(table)} '
                        f'AND column_name = {_sql_literal(args[0])}')
        except Error as e:
            warnings.warn(f"heliosdb_sqlite: could not record declared column types: {e}",
                          RuntimeWarning)

    def _catalog_ready(self, create: bool = False) -> bool:
        if not self._schema_bookkeeping:
            return False
        if self._catalog_exists:
            return True
        self._catalog_exists = self._table_exists(self.DECLTYPE_CATALOG)
        if not self._catalog_exists and create:
            self._internal_rows(
                f'CREATE TABLE IF NOT EXISTS {self.DECLTYPE_CATALOG} ('
                'table_name TEXT NOT NULL, column_name TEXT NOT NULL, decltype TEXT NOT NULL, '
                'column_label TEXT)')
            self._catalog_exists = True
            self._catalog_labels = True
        return bool(self._catalog_exists)

    def _catalog_write(self, table: str, columns: List[Tuple[str, ...]],
                       replace: bool = True) -> None:
        """Record ``(engine name, declared type[, name as written])`` for
        columns of ``table``. Columns without a declared type are recorded
        too when their name was written in mixed case (for
        cursor.description)."""
        entries = []
        for column in columns:
            name, decl = column[0], column[1]
            written = column[2] if len(column) > 2 else None
            label = written if written and written != name else None
            if decl or label:
                entries.append((name, decl or '', label))
        if not entries:
            return
        self._catalog_ready(create=True)
        labels = self._catalog_has_labels(add=True)
        names = ', '.join(_sql_literal(name) for name, _, _ in entries)
        where = f'table_name = {_sql_literal(table)}'
        self._internal_rows(f'DELETE FROM {self.DECLTYPE_CATALOG} WHERE {where}'
                            + ('' if replace else f' AND column_name IN ({names})'))
        if labels:
            rows = ', '.join(
                f'({_sql_literal(table)}, {_sql_literal(name)}, {_sql_literal(decl)}, '
                f'{"NULL" if label is None else _sql_literal(label)})'
                for name, decl, label in entries)
            self._internal_rows(f'INSERT INTO {self.DECLTYPE_CATALOG} '
                                f'(table_name, column_name, decltype, column_label) VALUES {rows}')
        else:
            rows = ', '.join(f'({_sql_literal(table)}, {_sql_literal(name)}, {_sql_literal(decl)})'
                             for name, decl, _ in entries)
            self._internal_rows(f'INSERT INTO {self.DECLTYPE_CATALOG} '
                                f'(table_name, column_name, decltype) VALUES {rows}')

    def _catalog_rows(self, table: str) -> Dict[str, Tuple[str, Optional[str]]]:
        """``{column: (declared type, name as declared)}`` recorded for
        ``table`` in the declared-type catalog."""
        cached = self._decltype_cache.get(table)
        if cached is not None:
            return cached
        rows: Dict[str, Tuple[str, Optional[str]]] = {}
        try:
            if self._catalog_ready():
                labels = self._catalog_has_labels()
                for row in self._internal_rows(
                        'SELECT column_name, decltype'
                        + (', column_label' if labels else '')
                        + f' FROM {self.DECLTYPE_CATALOG} '
                        f'WHERE table_name = {_sql_literal(table)}'):
                    if len(row) >= 2 and row[0] is not None and row[1] is not None:
                        label = row[2] if len(row) > 2 and row[2] is not None else None
                        rows[str(row[0])] = (str(row[1]), None if label is None else str(label))
        except Error:
            rows = {}
        self._decltype_cache[table] = rows
        return rows

    def _declared_types_of(self, table: str) -> Dict[str, str]:
        return {name: decl for name, (decl, _) in self._catalog_rows(table).items()}

    def _declared_labels_of(self, table: str) -> Dict[str, str]:
        if not self._declared_types:
            return {}
        return {name: label for name, (_, label) in self._catalog_rows(table).items() if label}

    def _catalog_has_labels(self, add: bool = False) -> bool:
        """The catalog has the column_label column. A catalog written by an
        older version gets it with ``add`` (when this layer records a
        CREATE / ALTER TABLE; never from a query)."""
        if self._catalog_labels:
            return True
        try:
            rows = self._internal_rows(
                'SELECT column_name FROM information_schema.columns '
                f'WHERE table_name = {_sql_literal(self.DECLTYPE_CATALOG)}')
            present = 'column_label' in {str(r[0]) for r in rows if r}
            if not present and add:
                self._internal_rows(f'ALTER TABLE {self.DECLTYPE_CATALOG} ADD COLUMN column_label TEXT')
                present = True
        except Error:
            present = False
        self._catalog_labels = present
        return present

    def _table_layout(self, table: str) -> Optional[List[Tuple[str, str, str]]]:
        """``[(engine column name, name as declared, udt_name), ...]`` of
        ``table`` in column order; None when the table is unknown."""
        if table in self._layout_cache:
            return self._layout_cache[table]
        layout: Optional[List[Tuple[str, str, str]]] = None
        try:
            rows = self._internal_rows(
                'SELECT column_name, ordinal_position, udt_name FROM information_schema.columns '
                f'WHERE table_name = {_sql_literal(table)}')
            rows = [r for r in rows if len(r) >= 3 and r[0] is not None]
            if rows:
                rows.sort(key=lambda r: int(r[1] or 0))
                labels = self._declared_labels_of(table)
                layout = [(str(r[0]), labels.get(str(r[0])) or str(r[0]), str(r[2] or '').lower())
                          for r in rows]
        except (Error, TypeError, ValueError):
            layout = None
        if len(self._layout_cache) > 256:
            self._layout_cache.clear()
        self._layout_cache[table] = layout
        return layout

    def _result_plan(self, stmt: str) -> Optional[_ResultPlan]:
        """How sqlite3 would name and type the result columns of ``stmt``
        (a SELECT), or None when the statement is beyond this description
        (the engine's names are used then)."""
        if not self._sqlite_types or (self._backend is None and self._mode != 'daemon'):
            return None
        if stmt in self._shape_cache:
            shape = self._shape_cache[stmt]
        else:
            try:
                shape = _sql.parse_select(stmt)
            except Exception:
                shape = None
            if len(self._shape_cache) > 128:
                self._shape_cache.clear()
            self._shape_cache[stmt] = shape
        if shape is None:
            return None
        tables = shape.tables
        layouts = {}
        for t in tables:
            if t.name is not None and t.name not in layouts:
                layouts[t.name] = self._table_layout(t.name)
        plan = _ResultPlan(stmt, shape)
        for index, item in enumerate(shape.items):
            if item.kind == 'star':
                chosen = [t for t in tables if item.qualifier is None or t.alias == item.qualifier]
                if not chosen or (item.qualifier is None and not shape.star_safe):
                    return None
                for t in chosen:
                    layout = layouts.get(t.name) if t.name is not None else None
                    if layout is None:
                        return None
                    for name, label, udt in layout:
                        plan.add(label, (t.name, name), 'column', index, name, t.alias, udt)
                continue
            if item.kind == 'column':
                source = None
                label = item.written
                if item.qualifier is not None:
                    matches = [t for t in tables if t.alias == item.qualifier]
                    if not matches:
                        matches = [t for t in tables if t.name == item.qualifier]
                else:
                    matches = [t for t in tables if t.name is not None and layouts.get(t.name)
                               and any(c[0] == item.column for c in layouts[t.name])]
                udt = None
                if len(matches) == 1 and matches[0].name is not None:
                    layout = layouts.get(matches[0].name) or []
                    for name, declared, udt_name in layout:
                        if name == item.column:
                            source = (matches[0].name, name)
                            label = declared
                            udt = udt_name
                            break
                key = item.alias if item.alias is not None else item.column
                plan.add(item.alias if item.alias is not None else label, source,
                         'column', index, key, None, udt)
                continue
            text = stmt[item.expr_start:item.expr_end]
            plan.add(item.alias if item.alias is not None else text, None, 'expr', index,
                     item.alias)
        if shape.compound:
            # Names come from the first SELECT; declared types do not apply.
            plan.sources = [None] * plan.width
            plan.udts = [None] * plan.width
        plan.finish()
        return plan

    def _plan_decltypes(self, plan: _ResultPlan) -> List[Optional[str]]:
        """sqlite3_column_decltype() of each result column: the source
        column's declared type ('' for an expression, which gets no
        converter); None for a column of a table created without this
        layer (its type OID is used instead)."""
        out: List[Optional[str]] = []
        for source, kind in zip(plan.sources, plan.kinds):
            if source is None:
                out.append('')
                continue
            declared = self._declared_types_of(source[0]) if self._declared_types else {}
            if declared:
                out.append(declared.get(source[1], ''))
            else:
                out.append(None)
        return out

    def _result_decltypes(self, sql: str, columns: List[Any]) -> List[Optional[str]]:
        """Declared type of each result column that names a column of a
        table in the statement (sqlite3_column_decltype); None otherwise."""
        if not self._declared_types or not columns:
            return [None] * len(columns)
        maps = [self._declared_types_of(t) for t in _sql.referenced_tables(sql)]
        maps = [m for m in maps if m]
        out: List[Optional[str]] = []
        for name in columns:
            found = {m[name] for m in maps if isinstance(name, str) and name in m}
            if len(found) == 1:
                out.append(found.pop())
            else:
                # Not a column of a recorded table (an expression, or
                # ambiguous): no declared type, as in sqlite3. With no
                # recorded table at all, fall back to the type OID.
                out.append('' if maps and not found else None)
        return out

    def _execute_embedded(self, sql: str) -> Union[Dict, int]:
        """Execute SQL in embedded REPL mode using persistent process."""
        import os

        # Ensure process is running
        if self._heliosdb_process is None or self._heliosdb_process.poll() is not None:
            self._start_persistent_repl()

        try:
            # Send SQL command to the REPL. The HeliosDB REPL waits for a
            # trailing `;` to terminate a statement (multi-line input is
            # buffered until then), so make sure one is present even when
            # the caller forgot. Backslash meta-commands (\d, \q, …) are
            # passed through verbatim.
            stmt = sql.rstrip()
            if stmt and not stmt.startswith('\\') and not stmt.endswith(';'):
                stmt += ';'
            self._heliosdb_process.stdin.write((stmt + '\n').encode('utf-8'))
            self._heliosdb_process.stdin.flush()

            # Read response with non-blocking I/O
            output = b''
            start_time = time.time()
            last_data_time = start_time

            # Completion patterns that indicate query finished
            completion_patterns = [
                'ms)',              # Timing display: "(0.5ms)"
                'row(s)',           # Row count: "1 row(s) affected"
                'rows)',            # "(5 rows)"
                'ERROR:',           # Error message
                'Query OK',         # Success message
                'CREATE TABLE',     # DDL success
                'DROP TABLE',       # DDL success
                'CREATE INDEX',     # DDL success
                'BEGIN',            # Transaction started
                'COMMIT',           # Transaction committed
                'ROLLBACK',         # Transaction rolled back
            ]

            while True:
                elapsed = time.time() - start_time
                if elapsed > self.timeout:
                    raise OperationalError(f"Query timeout after {self.timeout} seconds")

                try:
                    chunk = os.read(self._heliosdb_process.stdout.fileno(), 4096)
                    if chunk:
                        output += chunk
                        last_data_time = time.time()

                        # Check for completion patterns
                        output_str = output.decode('utf-8', errors='replace')
                        if any(p in output_str for p in completion_patterns):
                            # Wait a tiny bit to ensure we get the full line
                            time.sleep(0.02)
                            try:
                                extra = os.read(self._heliosdb_process.stdout.fileno(), 4096)
                                if extra:
                                    output += extra
                            except BlockingIOError:
                                pass
                            break
                except BlockingIOError:
                    # No data available
                    idle_time = time.time() - last_data_time
                    if output and idle_time > 0.15:
                        # Haven't received data for a bit, check if we have a complete response
                        output_str = output.decode('utf-8', errors='replace')
                        if any(p in output_str for p in completion_patterns):
                            break
                        # Also break if we see a newline after table closing (empty result)
                        if '└' in output_str or '╰' in output_str or '+--' in output_str:
                            break
                    time.sleep(0.01)
                    continue

            output_str = output.decode('utf-8', errors='replace')

            # Check for errors in output
            if 'ERROR:' in output_str or 'error:' in output_str:
                for line in output_str.split('\n'):
                    if 'ERROR:' in line or 'error:' in line:
                        raise DatabaseError(line.strip())

            # Parse output
            return self._parse_repl_output(output_str, sql)

        except (BrokenPipeError, OSError) as e:
            # Process died, restart it
            self._heliosdb_process = None
            raise DatabaseError(f"REPL process died: {e}")
        except Exception as e:
            if isinstance(e, (DatabaseError, OperationalError)):
                raise
            raise DatabaseError(f"Failed to execute SQL: {e}")

    def _daemon_connect_params(self) -> Tuple[str, Dict[str, Any]]:
        """libpq connection string and keyword overrides for daemon mode."""
        names = {
            'server_host': 'host',
            'server_port': 'port',
            'server_user': 'user',
            'server_password': 'password',
            'server_database': 'dbname',
        }
        if self._dsn:
            params = {names[k]: v for k, v in self._server_explicit.items()}
        else:
            params = {
                'host': self._server_host,
                'port': self._server_port,
                'user': self._server_user,
                'dbname': self._server_database,
            }
            if self._server_password is not None:
                params['password'] = self._server_password
        if not (self._dsn and 'connect_timeout' in self._dsn):
            # libpq treats 0 as "wait forever"; never round a short timeout
            # down to that.
            params['connect_timeout'] = max(1, int(round(self.timeout)))
        return self._dsn or '', params

    def _daemon_connection(self) -> Any:
        """The Connection's wire session, opened on first use.

        libpq autocommit is on: this layer sends BEGIN / COMMIT / ROLLBACK
        itself (see begin(), commit(), rollback()), exactly as in embedded
        mode. Every type is read as the text the server sent, so values are
        converted by the column type OIDs in one place (``_types``) instead
        of by psycopg2's own Python types.
        """
        conn = self._pg_conn
        if conn is not None:
            if not conn.closed:
                return conn
            self._pg_conn = None
            if self._in_transaction:
                self._in_transaction = False
                raise OperationalError(
                    "Lost the connection to the HeliosDB server; "
                    "the open transaction was rolled back"
                )

        try:
            import psycopg2
            import psycopg2.extensions
        except ImportError:
            raise InterfaceError(
                "Daemon mode needs psycopg2: pip install 'heliosdb-sqlite[daemon]' "
                "(or psycopg2-binary)"
            ) from None

        dsn, params = self._daemon_connect_params()
        try:
            conn = psycopg2.connect(dsn, **params)
        except psycopg2.Error as e:
            raise OperationalError(f"Cannot connect to the HeliosDB server: {e}") from None
        conn.autocommit = True
        text_types = psycopg2.extensions.new_type(
            tuple(psycopg2.extensions.string_types.keys()),
            'HELIOSDB_SQLITE_TEXT',
            lambda value, cursor: value,
        )
        psycopg2.extensions.register_type(text_types, conn)
        self._pg_conn = conn
        return conn

    # Savepoint that confines a failed statement inside a transaction, so
    # the transaction survives the error as it does in SQLite (PostgreSQL
    # semantics would abort the whole transaction).
    _STATEMENT_SAVEPOINT = 'heliosdb_sqlite_stmt'
    _TRANSACTION_KEYWORDS = frozenset(('BEGIN', 'START', 'COMMIT', 'END', 'ROLLBACK',
                                       'SAVEPOINT', 'RELEASE'))

    def _execute_daemon(self, sql: str) -> Union[Dict, int]:
        """Execute SQL over the PostgreSQL wire protocol on the session."""
        conn = self._daemon_connection()
        import psycopg2

        guard = (self._in_transaction and self._statement_savepoints
                 and _sql.first_keyword(sql) not in self._TRANSACTION_KEYWORDS)
        try:
            with conn.cursor() as cursor:
                if guard:
                    cursor.execute(f'SAVEPOINT {self._STATEMENT_SAVEPOINT}')
                try:
                    cursor.execute(sql)
                except psycopg2.Error:
                    if guard:
                        try:
                            cursor.execute(f'ROLLBACK TO SAVEPOINT {self._STATEMENT_SAVEPOINT}')
                            cursor.execute(f'RELEASE SAVEPOINT {self._STATEMENT_SAVEPOINT}')
                        except psycopg2.Error:
                            pass
                    raise
                if cursor.description is None:
                    result: Union[Dict, int] = cursor.rowcount
                else:
                    result = {
                        'rows': [list(row) for row in cursor.fetchall()],
                        'columns': [desc[0] for desc in cursor.description],
                        # RowDescription type OIDs, one per column
                        'types': [desc[1] for desc in cursor.description],
                    }
                if guard:
                    cursor.execute(f'RELEASE SAVEPOINT {self._STATEMENT_SAVEPOINT}')
                return result
        except psycopg2.Error as e:
            # psycopg2's classes follow the DB-API names; raise the class
            # sqlite3 raises for the same failure (engine errors that are not
            # integrity or data errors are OperationalError in sqlite3).
            error_class: type = DatabaseError
            for klass in type(e).__mro__:
                if klass.__name__ in _DRIVER_ERRORS:
                    error_class = _DRIVER_ERRORS[klass.__name__]
                    break
            if error_class in (ProgrammingError, InternalError):
                error_class = OperationalError
            raise error_class(str(e).strip()) from None

    def _parse_repl_output(self, output: str, sql: str) -> Union[Dict, int]:
        """
        Parse REPL output into structured results.

        Args:
            output: Raw REPL output
            sql: Original SQL statement

        Returns:
            Dict with rows/columns for SELECT, int for other statements
        """
        # Detect query type. PRAGMA may return rows (e.g. table_info) or
        # be a no-op tunable; in either case we let the table-parsing path
        # run so callers get column-shaped results back.
        sql_upper = sql.strip().upper()
        is_query = (
            sql_upper.startswith('SELECT')
            or sql_upper.startswith('WITH')
            or sql_upper.startswith('PRAGMA')
            or sql_upper.startswith('VALUES')
            or sql_upper.startswith('SHOW')
            or 'RETURNING' in sql_upper
        )

        if is_query:
            # Parse table output
            lines = output.strip().split('\n')

            # Find table boundaries (lines with various box drawing characters or dashes)
            # Support both Unicode box drawing and ASCII fallback
            separator_indices = []
            for i, line in enumerate(lines):
                # Check for Unicode box drawing horizontal lines
                if '─' in line or '━' in line or '═' in line:
                    separator_indices.append(i)
                # Check for ASCII-style separators
                elif line.strip().startswith('---') or line.strip().startswith('+--'):
                    separator_indices.append(i)

            if len(separator_indices) >= 2:
                # The HeliosDB REPL emits a box-drawn table where:
                #   - sep[0] is the top rule (┌──┐)
                #   - sep[1] is the header/data divider (├──┤)
                #   - sep[2..-1] are between-row dividers (when prettytable
                #     is set to FORMAT_BOX_CHARS, every data row gets one)
                #   - sep[-1] is the bottom rule (└──┘)
                # Header lives between sep[0] and sep[1]; data rows live
                # between sep[1] and sep[-1], excluding any intermediate
                # separators.
                top, header_div, bottom = separator_indices[0], separator_indices[1], separator_indices[-1]
                separator_set = set(separator_indices)

                columns = []
                for i in range(top + 1, header_div):
                    if i >= len(lines):
                        break
                    line = lines[i]
                    if '│' in line:
                        cells = [c.strip() for c in line.split('│') if c.strip()]
                    elif '|' in line:
                        cells = [c.strip() for c in line.split('|') if c.strip()]
                    else:
                        cells = line.split()
                    if cells:
                        columns = cells
                        break

                rows = []
                for i in range(header_div + 1, bottom):
                    if i in separator_set or i >= len(lines):
                        continue
                    row_line = lines[i]
                    if '│' in row_line:
                        values = [v.strip() for v in row_line.split('│') if v.strip()]
                    elif '|' in row_line:
                        values = [v.strip() for v in row_line.split('|') if v.strip()]
                    else:
                        continue
                    if values:
                        values = [None if v.upper() == 'NULL' else v for v in values]
                        rows.append(values)

                return {
                    'rows': rows,
                    'columns': columns,
                }
            else:
                # No table format - check for "(0 rows)" pattern indicating empty result
                if '(0 rows)' in output or 'row(s)' in output:
                    return {
                        'rows': [],
                        'columns': []
                    }
                # Return empty for no results
                return {
                    'rows': [],
                    'columns': []
                }
        else:
            # Parse row count from output
            # Look for patterns like "INSERT 0 1", "UPDATE 5", "1 row(s) affected"
            match = re.search(r'(\d+)\s+rows?\s*\)?(?:\s+affected)?', output, re.IGNORECASE)
            if match:
                return int(match.group(1))

            # Look for PostgreSQL-style command tags: "INSERT 0 N", "UPDATE N", "DELETE N"
            match = re.search(r'(?:INSERT|UPDATE|DELETE)\s+\d*\s*(\d+)', output, re.IGNORECASE)
            if match:
                return int(match.group(1))

            # Check for CREATE/DROP success indicators
            if any(p in output.upper() for p in ['CREATE TABLE', 'DROP TABLE', 'CREATE INDEX', 'CREATED', 'DROPPED']):
                return 1

            # Check for success indicators
            if 'successfully' in output.lower() or 'ok' in output.lower() or 'query ok' in output.lower():
                return 1

            return -1

    def cursor(self, factory: Optional[type] = None) -> Cursor:
        """
        Create a new cursor.

        Args:
            factory: Custom cursor factory

        Returns:
            Cursor object
        """
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        if factory is not None:
            return factory(self)
        return Cursor(self)

    def commit(self) -> None:
        """Commit current transaction."""
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        if self._in_transaction:
            try:
                self._execute_sql("COMMIT;")
            finally:
                self._in_transaction = False

    def rollback(self) -> None:
        """Rollback current transaction."""
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        if self._in_transaction:
            try:
                self._execute_sql("ROLLBACK;")
            finally:
                self._in_transaction = False

    def begin(self) -> None:
        """Begin explicit transaction."""
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        if not self._in_transaction:
            # HeliosDB uses standard SQL BEGIN (not SQLite's DEFERRED/IMMEDIATE/EXCLUSIVE)
            self._execute_sql("BEGIN;")
            self._in_transaction = True

    def close(self) -> None:
        """Close the database connection."""
        if not self._closed:
            pg_conn = getattr(self, '_pg_conn', None)
            try:
                if self._in_transaction:
                    if self._mode == 'daemon' and (pg_conn is None or pg_conn.closed):
                        # No live session: the server already discarded
                        # the transaction.
                        self._in_transaction = False
                    else:
                        self.rollback()
            finally:
                pg_conn = getattr(self, '_pg_conn', None)
                if pg_conn is not None:
                    self._pg_conn = None
                    try:
                        pg_conn.close()
                    except Exception:
                        pass

            backend = getattr(self, '_backend', None)
            if backend is not None:
                self._backend = None
                backend.close()

            # Terminate persistent REPL process if running
            if getattr(self, '_heliosdb_process', None) is not None:
                try:
                    # Send quit command gracefully (must encode to bytes)
                    self._heliosdb_process.stdin.write(b'\\q\n')
                    self._heliosdb_process.stdin.flush()
                    self._heliosdb_process.wait(timeout=2)
                except:
                    pass
                finally:
                    try:
                        self._heliosdb_process.terminate()
                    except:
                        pass
                    self._heliosdb_process = None

            self._closed = True

    def __del__(self) -> None:
        """Destructor to ensure process cleanup."""
        try:
            self.close()
        except:
            pass

    def execute(self, sql: str, parameters: Union[Tuple, Dict] = ()) -> Cursor:
        """
        Shortcut to create cursor and execute SQL.

        Args:
            sql: SQL statement
            parameters: Parameters for SQL

        Returns:
            Cursor with results
        """
        cursor = self.cursor()
        cursor.execute(sql, parameters)
        return cursor

    def executemany(self, sql: str, seq_of_parameters: List[Union[Tuple, Dict]]) -> Cursor:
        """
        Shortcut to create cursor and execute SQL many times.

        Args:
            sql: SQL statement
            seq_of_parameters: Sequence of parameters

        Returns:
            Cursor with results
        """
        cursor = self.cursor()
        cursor.executemany(sql, seq_of_parameters)
        return cursor

    def executescript(self, sql_script: str) -> Cursor:
        """
        Shortcut to create cursor and execute script.

        Args:
            sql_script: SQL script

        Returns:
            Cursor with results
        """
        cursor = self.cursor()
        cursor.executescript(sql_script)
        return cursor

    def create_function(self, name: str, num_params: int, func: Callable) -> None:
        """
        Create user-defined function.

        Note: HeliosDB may not support UDFs in embedded mode.
        This is provided for API compatibility.

        Args:
            name: Function name
            num_params: Number of parameters
            func: Python function to register
        """
        raise NotSupportedError("User-defined functions not yet supported in HeliosDB")

    def create_aggregate(self, name: str, num_params: int, aggregate_class: type) -> None:
        """
        Create user-defined aggregate function.

        Args:
            name: Aggregate name
            num_params: Number of parameters
            aggregate_class: Aggregate class
        """
        raise NotSupportedError("User-defined aggregates not yet supported in HeliosDB")

    def create_collation(self, name: str, callable_: Callable) -> None:
        """
        Create custom collation sequence.

        Args:
            name: Collation name
            callable_: Comparison function
        """
        raise NotSupportedError("Custom collations not yet supported in HeliosDB")

    def interrupt(self) -> None:
        """Interrupt long-running query."""
        # Send interrupt to HeliosDB process if running
        if self._heliosdb_process is not None and self._heliosdb_process.poll() is None:
            self._heliosdb_process.terminate()

    def set_authorizer(self, authorizer_callback: Optional[Callable]) -> None:
        """Set authorizer callback (no-op for compatibility)."""
        pass

    def set_progress_handler(self, handler: Optional[Callable], n: int) -> None:
        """Set progress handler (no-op for compatibility)."""
        pass

    def set_trace_callback(self, trace_callback: Optional[Callable]) -> None:
        """Set trace callback."""
        global _trace_callback
        _trace_callback = trace_callback

    def enable_load_extension(self, enabled: bool) -> None:
        """Enable extension loading (no-op for compatibility)."""
        pass

    def load_extension(self, path: str) -> None:
        """Load extension (not supported)."""
        raise NotSupportedError("Extensions not supported in HeliosDB")

    def iterdump(self) -> Iterator[str]:
        """
        Iterate over SQL dump of database.

        Returns:
            Iterator of SQL statements
        """
        # Execute .dump equivalent
        cursor = self.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        for row in cursor:
            yield row[0] + ';'

    def backup(self, target: 'Connection', pages: int = -1, progress: Optional[Callable] = None) -> None:
        """
        Backup database to target connection.

        Args:
            target: Target connection
            pages: Pages to copy (-1 for all)
            progress: Progress callback
        """
        # Use iterdump to backup
        for sql in self.iterdump():
            target.execute(sql)

    def __enter__(self) -> 'Connection':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Context manager exit."""
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    # =========================================================================
    # HeliosDB-Specific Extensions
    # =========================================================================

    def switch_to_server(self, port: int = 5432) -> None:
        """
        Switch from embedded mode to server mode.

        This starts HeliosDB as a daemon and switches the connection
        to use the PostgreSQL protocol.

        Args:
            port: Server port (default: 5432)
        """
        if self._mode != 'hybrid':
            raise NotSupportedError("switch_to_server only available in hybrid mode")

        # Start server daemon on the same data directory; the in-process
        # engine must let go of it first.
        data_dir = self._embedded_data_dir()
        if self._backend is not None:
            if self._in_transaction:
                self.commit()
            self._backend.close()
            self._backend = None
        cmd = [
            _resolve_binary(), 'start',
            '--data-dir', data_dir,
            '--port', str(port),
            '--daemon'
        ]

        try:
            subprocess.run(cmd, check=True, timeout=10)
            self._mode = 'daemon'
            self._server_port = port
            time.sleep(1)  # Wait for server to start
        except Exception as e:
            raise OperationalError(f"Failed to start server: {e}")

    def execute_vector_search(
        self,
        table: str,
        column: str,
        query_vector: List[float],
        limit: int = 10,
        metric: str = 'cosine'
    ) -> List[Tuple]:
        """
        Execute vector similarity search.

        Args:
            table: Table name
            column: Vector column name
            query_vector: Query vector
            limit: Number of results
            metric: Distance metric (cosine, l2, inner_product)

        Returns:
            List of result tuples
        """
        vector_str = '[' + ','.join(map(str, query_vector)) + ']'
        sql = f"""
            SELECT * FROM {table}
            ORDER BY {column} <-> '{vector_str}'::vector
            LIMIT {limit}
        """
        cursor = self.cursor()
        cursor.execute(sql)
        return cursor.fetchall()

    def create_branch(self, branch_name: str, from_branch: str = 'main') -> None:
        """
        Create database branch for isolation.

        Args:
            branch_name: New branch name
            from_branch: Source branch (default: main)
        """
        sql = f"CREATE DATABASE BRANCH {branch_name} FROM {from_branch} AS OF NOW;"
        self.execute(sql)

    def switch_branch(self, branch_name: str) -> None:
        """
        Switch to different database branch.

        Args:
            branch_name: Target branch name
        """
        # Note: Branch switching in HeliosDB may require reconnection
        raise NotSupportedError("Branch switching requires creating new connection")


# ============================================================================
# MODULE-LEVEL FUNCTIONS
# ============================================================================

def connect(
    database: str,
    timeout: float = 5.0,
    detect_types: int = 0,
    isolation_level: Optional[str] = "DEFERRED",
    check_same_thread: bool = True,
    factory: Optional[type] = None,
    cached_statements: int = 128,
    uri: bool = False,
    **kwargs
) -> Connection:
    """
    Open connection to HeliosDB database.

    This is the main entry point for the sqlite3 compatibility API.

    Args:
        database: Database path, or ':memory:' for in-memory
        timeout: Connection timeout in seconds
        detect_types: Type detection flags
        isolation_level: Transaction isolation level
        check_same_thread: Enforce single-threaded access
        factory: Custom Connection factory
        cached_statements: Statement cache size
        uri: Treat database as URI
        **kwargs: HeliosDB-specific options (mode, data_dir, server_port, etc.)

    Returns:
        Connection object

    Examples:
        >>> import heliosdb_sqlite as sqlite3
        >>> conn = sqlite3.connect('mydb.db')
        >>> cursor = conn.cursor()
        >>> cursor.execute("CREATE TABLE users (id INT, name TEXT)")
        >>> conn.commit()
        >>> conn.close()
    """
    if factory is not None:
        return factory(
            database,
            timeout=timeout,
            detect_types=detect_types,
            isolation_level=isolation_level,
            check_same_thread=check_same_thread,
            cached_statements=cached_statements,
            uri=uri,
            **kwargs
        )

    return Connection(
        database,
        timeout=timeout,
        detect_types=detect_types,
        isolation_level=isolation_level,
        check_same_thread=check_same_thread,
        factory=factory,
        cached_statements=cached_statements,
        uri=uri,
        **kwargs
    )


# ============================================================================
# MAIN LIBRARY EXPORTED API
# ============================================================================

__all__ = [
    # Core classes
    'Connection',
    'Cursor',
    'Row',

    # Functions
    'connect',
    'register_adapter',
    'register_converter',

    # Exceptions
    'Error',
    'Warning',
    'DatabaseError',
    'IntegrityError',
    'ProgrammingError',
    'OperationalError',
    'NotSupportedError',
    'InterfaceError',
    'InternalError',
    'DataError',

    # Constants
    'apilevel',
    'threadsafety',
    'paramstyle',
    'PARSE_DECLTYPES',
    'PARSE_COLNAMES',
    'SQLITE_OK',
    'SQLITE_ERROR',
    'SQLITE_DENY',
    'SQLITE_IGNORE',

    # Type converters
    'Binary',
    'Date',
    'Time',
    'Timestamp',
    'DateFromTicks',
    'TimeFromTicks',
    'TimestampFromTicks',

    # Version info
    'sqlite_version',
    'sqlite_version_info',
    'version',
    'version_info',

    # Advanced
    'enable_callback_tracebacks',
    'complete_statement',
    'register_trace_callback',
]
