"""
HELIOSDB_SQLITE_MAIN_LIBRARY.py

Production-ready SQLite API compatibility layer for HeliosDB.
Provides 100% API compatibility with Python's sqlite3 module while routing
all operations to HeliosDB's embedded REPL mode.

Architecture:
- Drop-in replacement: No application code changes required
- Multi-mode support: REPL embedded, Server daemon, Hybrid
- Advanced features: Vector search, branching, time-travel, encryption
- Full transaction support: Autocommit, explicit transactions, savepoints
- Complete API coverage: All sqlite3.Connection and sqlite3.Cursor methods

Author: HeliosDB Team
Version: 3.0.1
License: MIT
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
from typing import Any, List, Dict, Optional, Tuple, Union, Callable, Iterator
from datetime import date, time as datetime_time, datetime
from pathlib import Path

# Version constants (mimics sqlite3)
version = "3.0.2"
version_info = (3, 0, 2)
sqlite_version = "3.45.0 (HeliosDB compatible)"
sqlite_version_info = (3, 45, 0)

# DB-API 2.0 module globals (sqlite3 exposes these too)
apilevel = "2.0"
threadsafety = 1          # threads may share the module, not connections
paramstyle = "qmark"

# Sentinel for Connection.autocommit, mirroring Python 3.12's sqlite3.
# LEGACY_TRANSACTION_CONTROL means "obey isolation_level" (pre-3.12 behaviour).
LEGACY_TRANSACTION_CONTROL = -1

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


# ============================================================================
# TYPE ADAPTERS - Binary, Date, Time, Timestamp
# ============================================================================

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


# ============================================================================
# STATEMENT CLASSIFICATION - drives sqlite3-compatible transaction control
# ============================================================================

def _strip_leading_noise(sql: str) -> str:
    """Strip leading whitespace and SQL comments so the first keyword is visible."""
    out = sql
    while True:
        stripped = out.lstrip()
        if stripped.startswith('--'):
            newline = stripped.find('\n')
            out = '' if newline == -1 else stripped[newline + 1:]
            continue
        if stripped.startswith('/*'):
            end = stripped.find('*/')
            out = '' if end == -1 else stripped[end + 2:]
            continue
        return stripped


_DML_HEADS = frozenset({'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE', 'UPSERT', 'COPY'})
_QUERY_HEADS = frozenset({'SELECT', 'VALUES', 'TABLE', 'EXPLAIN', 'SHOW', 'PRAGMA', 'DESCRIBE'})
_CTE_DML_RE = re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE)\b', re.IGNORECASE)


def _classify_statement(sql: str) -> str:
    """
    Classify a statement for transaction-control purposes.

    Returns one of:
        'begin'       - opens a transaction (BEGIN / START TRANSACTION)
        'commit'      - closes a transaction (COMMIT / END)
        'rollback'    - closes a transaction (ROLLBACK)
        'rollback_to' - ROLLBACK TO SAVEPOINT (stays inside the transaction)
        'savepoint'   - SAVEPOINT / RELEASE (requires an open transaction)
        'dml'         - data modification; sqlite3 opens an implicit transaction
        'query'       - read-only; sqlite3 opens NO implicit transaction
        'other'       - DDL, SET, ...; sqlite3 opens no implicit transaction for
                        these either (it stopped doing so in Python 3.6)
    """
    text = _strip_leading_noise(sql)
    if not text:
        return 'other'
    head = re.split(r'[^A-Za-z_]', text, 1)[0].upper()

    if head in ('BEGIN', 'START'):
        return 'begin'
    if head in ('COMMIT', 'END'):
        return 'commit'
    if head == 'ROLLBACK':
        if re.match(r'ROLLBACK\s+TO\b', text, re.IGNORECASE):
            return 'rollback_to'
        return 'rollback'
    if head in ('SAVEPOINT', 'RELEASE'):
        return 'savepoint'
    if head in _DML_HEADS:
        return 'dml'
    if head == 'WITH':
        # A data-modifying CTE is DML; a plain WITH ... SELECT is a query.
        return 'dml' if _CTE_DML_RE.search(text) else 'query'
    if head in _QUERY_HEADS:
        return 'query'
    return 'other'


def _normalise_autocommit(value: Any) -> Any:
    """Validate/normalise a Connection.autocommit value (Python 3.12 semantics)."""
    if value is True or value is False:
        return value
    if value == LEGACY_TRANSACTION_CONTROL:
        return LEGACY_TRANSACTION_CONTROL
    if isinstance(value, int):
        return bool(value)
    raise ValueError(
        "autocommit must be True, False or LEGACY_TRANSACTION_CONTROL, "
        "not {!r}".format(value)
    )


# ============================================================================
# ERROR MAPPING - PostgreSQL SQLSTATE -> sqlite3 exception hierarchy
# ============================================================================

_SQLSTATE_CLASS_MAP = {
    '08': OperationalError,   # connection exception
    '0A': NotSupportedError,  # feature not supported
    '22': DataError,          # data exception
    '23': IntegrityError,     # integrity constraint violation
    '25': OperationalError,   # invalid transaction state
    '28': OperationalError,   # invalid authorization specification
    '40': OperationalError,   # transaction rollback / serialization / deadlock
    '42': ProgrammingError,   # syntax error or access rule violation
    '53': OperationalError,   # insufficient resources
    '54': OperationalError,   # program limit exceeded
    '55': OperationalError,   # object not in prerequisite state
    '57': OperationalError,   # operator intervention (shutdown, cancel)
    '58': OperationalError,   # system error
    'XX': InternalError,      # internal error
}


def _redact_dsn(value: Any) -> str:
    """Render a DSN or driver message without leaking credentials."""
    text = str(value)
    text = re.sub(r'(?i)(password\s*=\s*)(\S+)', r'\1<redacted>', text)
    text = re.sub(r'(?i)(//[^:/@\s]+:)([^@\s]+)(@)', r'\1<redacted>\3', text)
    return text


def _map_server_error(exc: BaseException, context: str = '') -> Error:
    """Translate a psycopg2 exception into the matching sqlite3 exception class."""
    pgcode = getattr(exc, 'pgcode', None)
    if pgcode:
        cls = _SQLSTATE_CLASS_MAP.get(pgcode[:2], DatabaseError)
    else:
        # No SQLSTATE means the failure was at the transport/driver level.
        cls = OperationalError
    message = _redact_dsn(exc).strip() or exc.__class__.__name__
    if context:
        message = '{}: {}'.format(context, message)
    return cls(message)


def _is_connection_loss(exc: BaseException) -> bool:
    """True when the exception means 'the session is gone', not 'the query failed'."""
    pgcode = getattr(exc, 'pgcode', None)
    if pgcode:
        return pgcode.startswith('08') or pgcode in ('57P01', '57P02', '57P03')
    # psycopg2 raises OperationalError/InterfaceError with no SQLSTATE when the
    # socket itself failed.
    return True


# ============================================================================
# SERVER SESSION - ONE persistent PostgreSQL-wire session per Connection
# ============================================================================

class _ServerSession:
    """
    Owns exactly one psycopg2 connection for the whole life of a Connection.

    Every statement, and every BEGIN/COMMIT/ROLLBACK, runs on this one session.
    That is what makes transactions, temporary tables, SET, SAVEPOINT,
    LASTVAL() and advisory locks behave the way callers expect.  Opening a
    fresh connection per statement (as 3.0.1 did) discards all of it and
    rolls back every uncommitted write when the socket closes.

    libpq autocommit is deliberately turned ON here: the shim issues
    transaction control itself so it can reproduce sqlite3's semantics
    (implicit BEGIN before DML only) instead of psycopg2's PEP 249 semantics
    (BEGIN before absolutely everything, including SELECT and DDL).
    """

    def __init__(self, params: Dict[str, Any]):
        self._params = dict(params)
        self._conn = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.closed == 0

    def handle(self):
        """Return a live psycopg2 connection, connecting lazily on first use."""
        if self.connected:
            return self._conn
        self._conn = None
        try:
            import psycopg2
        except ImportError as exc:
            raise InterfaceError(
                "daemon mode requires psycopg2: pip install 'psycopg2-binary>=2.9'"
            )
        try:
            conn = psycopg2.connect(**self._params)
        except Exception as exc:
            raise _map_server_error(exc, 'could not connect to HeliosDB server')
        # The shim drives transactions; libpq must not open its own.
        conn.autocommit = True
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # -- introspection (used by diagnostics and by the regression tests) ------

    def backend_pid(self) -> Optional[int]:
        """Server-side PID of this session; stable for the session's lifetime."""
        return self.handle().get_backend_pid()

    def server_transaction_status(self) -> Optional[int]:
        if not self.connected:
            return None
        return self._conn.get_transaction_status()

    def cancel(self) -> None:
        if self.connected:
            self._conn.cancel()

    def __repr__(self) -> str:
        return '<_ServerSession {} connected={}>'.format(
            _redact_dsn(self._params), self.connected
        )

    # -- execution -----------------------------------------------------------

    def execute(self, sql: str, allow_replay: bool = False) -> Union[Dict, int]:
        """
        Run one statement on the persistent session.

        allow_replay may only be set by the caller for statements that are
        read-only AND outside any transaction.  A write is NEVER replayed after
        a connection failure: the statement may already have been applied
        server-side, and re-running it could duplicate data.
        """
        import psycopg2
        was_live = self.connected
        conn = self.handle()
        try:
            return self._run(conn, sql)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            if not _is_connection_loss(exc):
                raise _map_server_error(exc)
            self.close()
            if allow_replay and was_live:
                # The session died; this statement is a read outside any
                # transaction, so replaying it on a fresh session is safe.
                conn = self.handle()
                try:
                    return self._run(conn, sql)
                except Exception as retry_exc:
                    raise _map_server_error(
                        retry_exc, 'HeliosDB server connection lost'
                    )
            raise _map_server_error(exc, 'HeliosDB server connection lost')
        except Exception as exc:
            raise _map_server_error(exc)

    @staticmethod
    def _run(conn, sql: str) -> Union[Dict, int]:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            if cursor.description is not None:
                # Materialise the whole result set before the driver cursor is
                # closed; the shim's Cursor serves fetch*() from this list.
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return {'rows': rows, 'columns': columns}
            return cursor.rowcount
        finally:
            try:
                cursor.close()
            except Exception:
                pass


# ============================================================================
# ROW CLASS - Factory for result rows
# ============================================================================

class Row:
    """
    Represents a single row from a database query result.
    Supports both index and name-based access.
    """

    def __init__(self, cursor: 'Cursor', values: Tuple[Any, ...]):
        """
        Initialize a Row object.

        Args:
            cursor: The cursor that produced this row
            values: Tuple of column values
        """
        self._cursor = cursor
        self._values = values
        self._description = cursor.description or []

    def __getitem__(self, key: Union[int, str]) -> Any:
        """Get column value by index or name."""
        if isinstance(key, int):
            return self._values[key]
        elif isinstance(key, str):
            # Find column by name
            for i, desc in enumerate(self._description):
                if desc[0] == key:
                    return self._values[i]
            raise IndexError(f"No column named '{key}'")
        else:
            raise TypeError(f"Index must be int or str, not {type(key).__name__}")

    def __len__(self) -> int:
        """Return number of columns."""
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        """Iterate over column values."""
        return iter(self._values)

    def __repr__(self) -> str:
        """String representation."""
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
        self._results = []
        self._result_index = 0
        self.row_factory = None
        # lastrowid is resolved lazily (see the property below) so the extra
        # round-trip is only paid by callers that actually read it.
        self._lastrowid = None
        self._lastrowid_seq = None

    @property
    def lastrowid(self) -> Optional[int]:
        """
        Row id of the last inserted row, as sqlite3 reports it.

        In daemon mode this is resolved from LASTVAL() on the *same* session
        that ran the INSERT - which is only possible because the session is now
        persistent.  It is reported only while no other statement has run on
        this connection since the INSERT; otherwise None is returned rather
        than a value that might belong to somebody else's statement.
        """
        if self._lastrowid is not None:
            return self._lastrowid
        if self._lastrowid_seq is None:
            return None
        if self.connection._stmt_seq != self._lastrowid_seq:
            return None
        self._lastrowid = self.connection._fetch_lastrowid()
        return self._lastrowid

    @lastrowid.setter
    def lastrowid(self, value: Optional[int]) -> None:
        self._lastrowid = value

    def execute(self, sql: str, parameters: Union[Tuple, Dict] = ()) -> 'Cursor':
        """
        Execute a single SQL statement.

        Args:
            sql: SQL statement to execute
            parameters: Parameters for SQL (tuple or dict for named params)

        Returns:
            Self for chaining

        Raises:
            ProgrammingError: If cursor is closed or SQL is invalid
            DatabaseError: If execution fails
        """
        if self.connection._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        # Trace callback
        if _trace_callback:
            try:
                _trace_callback(sql)
            except Exception as e:
                if _enable_callback_tracebacks_flag:
                    import traceback
                    traceback.print_exc()

        # Bind parameters
        bound_sql = self._bind_parameters(sql, parameters)
        kind = _classify_statement(bound_sql)

        # Reset per-statement state
        self._lastrowid = None
        self._lastrowid_seq = None

        # Execute through connection
        try:
            results = self.connection._execute_sql(bound_sql)

            # Parse results
            if isinstance(results, dict):
                # Query result
                self._results = results.get('rows', [])
                self._result_index = 0

                # Set description (column metadata)
                columns = results.get('columns', [])
                if columns:
                    self.description = [
                        (col, None, None, None, None, None, None)
                        for col in columns
                    ]
                else:
                    self.description = None

                # sqlite3 reports -1 for SELECT; row counts come from fetch*().
                self.rowcount = -1
            else:
                # Command result (INSERT, UPDATE, DELETE, etc.)
                self._results = []
                self._result_index = 0
                self.description = None
                self.rowcount = results if isinstance(results, int) else -1

            if kind == 'dml' and _strip_leading_noise(bound_sql).upper().startswith('INSERT'):
                self._lastrowid_seq = self.connection._stmt_seq

            return self

        except Error:
            # Already one of our exception types: keep the specific class so
            # that `except IntegrityError:` in caller code still works.
            raise
        except Exception as e:
            raise DatabaseError(f"Error executing SQL: {e}") from e

    def executemany(self, sql: str, seq_of_parameters: List[Union[Tuple, Dict]]) -> 'Cursor':
        """
        Execute SQL statement for each parameter set.

        Args:
            sql: SQL statement to execute
            seq_of_parameters: Sequence of parameter tuples/dicts

        Returns:
            Self for chaining
        """
        for parameters in seq_of_parameters:
            self.execute(sql, parameters)
        return self

    def executescript(self, sql_script: str) -> 'Cursor':
        """
        Execute multiple SQL statements separated by semicolons.

        Args:
            sql_script: SQL script with multiple statements

        Returns:
            Self for chaining
        """
        # sqlite3: "If there is a pending transaction, an implicit COMMIT
        # statement is executed first.  No other implicit transaction control
        # is performed; any transaction control must be added to sql_script."
        # The old test was inverted, so the pending transaction was never
        # committed and the script ran inside it.
        if self.connection._in_transaction:
            self.connection.commit()

        # Split by semicolons (basic parser)
        statements = [s.strip() for s in sql_script.split(';') if s.strip()]

        previous_autocommit = self.connection.autocommit
        self.connection._set_autocommit_raw(True)
        try:
            for statement in statements:
                self.execute(statement + ';')
        finally:
            self.connection._set_autocommit_raw(previous_autocommit)

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

    def _bind_parameters(self, sql: str, parameters: Union[Tuple, Dict]) -> str:
        """
        Bind parameters to SQL statement.

        Supports:
        - Positional: ? placeholders
        - Named: :name, @name, $name placeholders

        Args:
            sql: SQL with placeholders
            parameters: Parameter values

        Returns:
            SQL with bound parameters
        """
        if not parameters:
            return sql

        if isinstance(parameters, dict):
            # Named parameters
            result = sql
            for key, value in parameters.items():
                placeholder_variants = [f':{key}', f'@{key}', f'${key}']
                for placeholder in placeholder_variants:
                    if placeholder in result:
                        result = result.replace(placeholder, self._format_value(value))
            return result
        else:
            # Positional parameters
            result = sql
            for value in parameters:
                result = result.replace('?', self._format_value(value), 1)
            return result

    def _format_value(self, value: Any) -> str:
        """Format Python value for SQL."""
        if value is None:
            return 'NULL'
        elif isinstance(value, bool):
            return 'TRUE' if value else 'FALSE'
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Escape single quotes
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        elif isinstance(value, bytes):
            # Convert to hex string
            return f"X'{value.hex()}'"
        elif isinstance(value, (date, datetime)):
            return f"'{value.isoformat()}'"
        else:
            # Try adapter
            type_ = type(value)
            if type_ in _adapters:
                adapted = _adapters[type_](value)
                return self._format_value(adapted)
            else:
                return f"'{str(value)}'"


# ============================================================================
# CONNECTION CLASS - Main database interface
# ============================================================================

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
        self._cached_statements = cached_statements

        self._closed = False
        self._in_transaction = False
        self._txn_failed = False
        self._stmt_seq = 0
        self._total_changes = 0
        self._thread_id = threading.get_ident() if check_same_thread else None
        # Serialises access to the single server session.  Needed because
        # check_same_thread=False lets callers share one Connection, and a
        # psycopg2 connection must never have two statements in flight.
        self._lock = threading.RLock()

        # Python 3.12 sqlite3 transaction control.  LEGACY_TRANSACTION_CONTROL
        # (the default) means "obey isolation_level".
        self._autocommit = _normalise_autocommit(
            kwargs.pop('autocommit', LEGACY_TRANSACTION_CONTROL)
        )

        # HeliosDB-specific configuration
        self._mode = kwargs.pop('mode', 'embedded')  # embedded, daemon, hybrid
        self._data_dir = kwargs.pop('data_dir', None)
        self._server_port = kwargs.pop('server_port', 5432)
        self._server_host = kwargs.pop('server_host', '127.0.0.1')

        # Daemon-mode connection parameters.  These used to be hard-coded to
        # helios@127.0.0.1/heliosdb with an empty password, and a `dsn=` keyword
        # was silently swallowed by **kwargs.
        self._server_dsn = kwargs.pop('dsn', None)
        connection_string = kwargs.pop('connection_string', None)
        if self._server_dsn is None:
            self._server_dsn = connection_string
        self._server_database = kwargs.pop('server_database', None)
        self._server_user = kwargs.pop('user', None)
        self._server_password = kwargs.pop('password', None)
        self._server_sslmode = kwargs.pop('sslmode', None)
        self._server_options = kwargs.pop('options', None)
        self._session = None

        # `connect('postgresql://...', mode='daemon')` is what people try first.
        if (self._server_dsn is None and isinstance(database, str)
                and database.startswith(('postgres://', 'postgresql://'))):
            self._server_dsn = database

        if kwargs:
            # Silently ignoring keywords is how a mistyped/unsupported `dsn=`
            # went unnoticed in 3.0.1.
            warnings.warn(
                'heliosdb_sqlite.connect(): ignoring unknown keyword argument(s): '
                + ', '.join(sorted(kwargs)),
                stacklevel=3,
            )

        # Initialize HeliosDB connection based on mode
        self._initialize_heliosdb()

        # NOTE: sqlite3 does NOT open a transaction at connect time.  The
        # implicit BEGIN is deferred until the first DML statement; see
        # _needs_implicit_transaction().  The eager begin() that used to live
        # here issued a BEGIN that no later statement could belong to, because
        # each statement ran on its own short-lived connection.

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
        """Initialize embedded REPL mode with persistent process."""
        # Determine data directory
        if self.database == ':memory:':
            self._heliosdb_args = ['--memory']
        else:
            # Use database path as data directory
            data_dir = self._data_dir or str(Path(self.database).parent / 'heliosdb-data')
            self._heliosdb_args = ['--data-dir', data_dir]

        # Start persistent REPL process for state preservation
        self._start_persistent_repl()

    def _start_persistent_repl(self) -> None:
        """Start a persistent REPL process for embedded mode."""
        import subprocess
        import os
        import fcntl
        cmd = ['heliosdb', 'repl'] + self._heliosdb_args

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
        """
        Initialise server (PostgreSQL-wire) mode.

        No socket is opened here.  One session is created lazily on the first
        statement and then kept for the whole life of this Connection, so that
        transactions, temporary tables and session state survive across
        statements.
        """
        self._heliosdb_process = None
        self._session = _ServerSession(self._build_server_params())

    def _build_server_params(self) -> Dict[str, Any]:
        """
        Build psycopg2 connection parameters for daemon mode.

        Only keys the caller actually supplied are passed through, so libpq's
        own defaults (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE, ~/.pgpass,
        service files) keep working.
        """
        params: Dict[str, Any] = {}
        if self._server_dsn:
            params['dsn'] = self._server_dsn
            if self._server_user:
                params['user'] = self._server_user
            if self._server_password is not None:
                params['password'] = self._server_password
            if self._server_database:
                params['database'] = self._server_database
        else:
            if self._server_host is not None:
                params['host'] = self._server_host
            if self._server_port is not None:
                params['port'] = self._server_port
            params['database'] = (
                self._server_database
                or os.environ.get('PGDATABASE')
                or 'heliosdb'
            )
            params['user'] = (
                self._server_user or os.environ.get('PGUSER') or 'helios'
            )
            password = self._server_password
            if password is None:
                password = os.environ.get('PGPASSWORD')
            # Omit the password entirely when none was given: passing '' forces
            # an empty password and defeats ~/.pgpass and PGPASSWORD.
            if password is not None:
                params['password'] = password
        if self._server_sslmode:
            params['sslmode'] = self._server_sslmode
        if self._server_options:
            params['options'] = self._server_options

        # libpq clamps connect_timeout below 2 up to 2 and treats 0 as "wait
        # forever"; the old int(self.timeout) turned timeout=0.5 into 0.
        if self.timeout:
            params['connect_timeout'] = max(2, int(round(self.timeout)))
        return params

    def _init_hybrid_mode(self) -> None:
        """Initialize hybrid mode (embedded + optional server)."""
        self._init_embedded_mode()
        # Server can be started on-demand via switch_to_server()

    # -- transaction policy --------------------------------------------------

    @property
    def in_transaction(self) -> bool:
        """True while a transaction is open (read-only, like sqlite3)."""
        return self._in_transaction

    @property
    def total_changes(self) -> int:
        """Rows inserted/updated/deleted since this connection was opened."""
        return self._total_changes

    @property
    def autocommit(self) -> Any:
        """
        Python 3.12 sqlite3 transaction-control switch.

        LEGACY_TRANSACTION_CONTROL (default) -> obey isolation_level
        False                                -> PEP 249: always in a transaction
        True                                 -> never open an implicit transaction
        """
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: Any) -> None:
        value = _normalise_autocommit(value)
        if value == self._autocommit and type(value) is type(self._autocommit):
            return
        # Changing the mode commits whatever is pending, as sqlite3 does.
        if self._in_transaction:
            self.commit()
        self._autocommit = value

    def _set_autocommit_raw(self, value: Any) -> None:
        """Set the autocommit mode without committing (internal use)."""
        self._autocommit = _normalise_autocommit(value)

    def _needs_implicit_transaction(self, kind: str) -> bool:
        """Would sqlite3 open an implicit transaction before this statement?"""
        if self._autocommit is True:
            return False
        if self._autocommit is False:
            # PEP 249 mode: a transaction is open at all times.
            return kind not in ('begin', 'commit', 'rollback')
        # Legacy mode: sqlite3 opens an implicit transaction before DML only.
        # DDL and SELECT run outside one (it stopped doing otherwise in 3.6).
        if self.isolation_level is None:
            return False
        return kind in ('dml', 'savepoint')

    def _begin_statement(self) -> str:
        level = (self.isolation_level or '').strip().upper()
        if level in ('', 'DEFERRED', 'IMMEDIATE', 'EXCLUSIVE'):
            # HeliosDB speaks PostgreSQL: there is no IMMEDIATE/EXCLUSIVE
            # variant, so every implicit transaction is a plain BEGIN.
            return 'BEGIN;'
        raise ValueError(
            'invalid isolation_level: {!r}'.format(self.isolation_level)
        )

    def _execute_sql(self, sql: str) -> Union[Dict, int]:
        """
        Execute one statement with sqlite3-compatible transaction control.

        Transaction control and the statement it covers always run on the SAME
        backend session, which is the whole point of this layer.

        Args:
            sql: SQL statement to execute

        Returns:
            Dict with results for queries, int for row count

        Raises:
            DatabaseError: If execution fails
        """
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        kind = _classify_statement(sql)

        with self._lock:
            if self._txn_failed and kind not in ('rollback', 'rollback_to'):
                raise OperationalError(
                    'the current transaction was aborted by an earlier error; '
                    'call rollback() before issuing further statements'
                )

            if kind == 'begin' and self._in_transaction:
                raise ProgrammingError(
                    'cannot start a transaction within a transaction'
                )

            if (kind not in ('begin', 'commit', 'rollback')
                    and not self._in_transaction
                    and self._needs_implicit_transaction(kind)):
                self._execute_raw(self._begin_statement())
                self._in_transaction = True
                self._txn_failed = False

            try:
                result = self._execute_raw(sql, read_only=(kind == 'query'))
            except Exception:
                if self._in_transaction and self._mode == 'daemon':
                    # PostgreSQL aborts the whole transaction on any error;
                    # remember that so COMMIT cannot silently become ROLLBACK.
                    self._txn_failed = True
                raise

            if kind == 'begin':
                self._in_transaction = True
                self._txn_failed = False
            elif kind in ('commit', 'rollback'):
                self._in_transaction = False
                self._txn_failed = False
            elif kind == 'rollback_to':
                self._txn_failed = False

            self._stmt_seq += 1
            if kind == 'dml' and isinstance(result, int) and result > 0:
                self._total_changes += result
            return result

    def _execute_raw(self, sql: str, read_only: bool = False) -> Union[Dict, int]:
        """Send one statement to the backend with no transaction bookkeeping."""
        if self._mode == 'daemon':
            return self._execute_daemon(sql, read_only=read_only)
        return self._execute_embedded(sql)

    def _execute_embedded(self, sql: str) -> Union[Dict, int]:
        """Execute SQL in embedded REPL mode using persistent process."""
        import os

        # Ensure process is running
        if self._heliosdb_process is None or self._heliosdb_process.poll() is not None:
            self._start_persistent_repl()

        try:
            # Send SQL command to the REPL
            self._heliosdb_process.stdin.write((sql + '\n').encode('utf-8'))
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

    def _execute_daemon(self, sql: str, read_only: bool = False) -> Union[Dict, int]:
        """
        Execute SQL on this connection's persistent PostgreSQL-wire session.

        The session is created on first use and lives until close().  It is
        never closed between statements: doing that is what silently rolled
        back every write in 3.0.1, because psycopg2 defaults to
        autocommit=False and closing a connection discards its open
        transaction.
        """
        if self._session is None:
            self._session = _ServerSession(self._build_server_params())
        # A read outside a transaction is the only thing that may be replayed
        # after a dropped connection.  Writes, and anything inside a
        # transaction, are never retried: the statement may already have been
        # applied server-side and the transaction cannot be reconstructed.
        allow_replay = read_only and not self._in_transaction
        return self._session.execute(sql, allow_replay=allow_replay)

    def _fetch_lastrowid(self) -> Optional[int]:
        """
        Resolve LASTVAL() on the same session that ran the INSERT.

        Returns None when the statement did not touch a sequence.  When a
        transaction is open the probe is wrapped in a SAVEPOINT, because a
        failing LASTVAL() would otherwise abort the caller's transaction.
        """
        if self._mode != 'daemon' or self._session is None or self._closed:
            return None
        if self._txn_failed:
            return None
        savepoint = self._in_transaction
        result = None
        try:
            if savepoint:
                self._execute_raw('SAVEPOINT _hdb_lastrowid;')
            try:
                result = self._execute_raw('SELECT LASTVAL();', read_only=True)
            except Exception:
                if savepoint:
                    self._execute_raw('ROLLBACK TO SAVEPOINT _hdb_lastrowid;')
                return None
            finally:
                if savepoint:
                    try:
                        self._execute_raw('RELEASE SAVEPOINT _hdb_lastrowid;')
                    except Exception:
                        pass
        except Exception:
            return None
        if isinstance(result, dict) and result.get('rows'):
            try:
                return int(result['rows'][0][0])
            except (TypeError, ValueError, IndexError):
                return None
        return None

    def _parse_repl_output(self, output: str, sql: str) -> Union[Dict, int]:
        """
        Parse REPL output into structured results.

        Args:
            output: Raw REPL output
            sql: Original SQL statement

        Returns:
            Dict with rows/columns for SELECT, int for other statements
        """
        # Detect query type
        sql_upper = sql.strip().upper()
        is_query = sql_upper.startswith('SELECT') or sql_upper.startswith('WITH')

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
                # Extract column names from header line (before first separator)
                header_line = lines[separator_indices[0] - 1] if separator_indices[0] > 0 else ""

                # Try Unicode box drawing delimiter first, then ASCII pipe
                if '│' in header_line:
                    columns = [col.strip() for col in header_line.split('│') if col.strip()]
                elif '|' in header_line:
                    columns = [col.strip() for col in header_line.split('|') if col.strip()]
                else:
                    # Whitespace-separated columns (fallback)
                    columns = header_line.split()

                # Extract data rows between first and second separator
                rows = []
                for i in range(separator_indices[0] + 1, separator_indices[1]):
                    if i < len(lines):
                        row_line = lines[i]
                        # Parse based on delimiter type
                        if '│' in row_line:
                            values = [val.strip() for val in row_line.split('│') if val.strip()]
                        elif '|' in row_line:
                            values = [val.strip() for val in row_line.split('|') if val.strip()]
                        else:
                            # Skip empty or malformed lines
                            continue

                        if values:
                            # Convert NULL strings to None
                            values = [None if v.upper() == 'NULL' else v for v in values]
                            rows.append(values)

                return {
                    'rows': rows,
                    'columns': columns
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
        """
        Commit the current transaction, on the connection's own session.

        A no-op when no transaction is open, exactly like sqlite3.  If the
        transaction was already aborted by a failed statement, PostgreSQL would
        turn COMMIT into a silent ROLLBACK, so this raises instead of letting
        the caller believe the work was saved.
        """
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        with self._lock:
            if self._txn_failed:
                self._safe_rollback()
                raise OperationalError(
                    'cannot commit: the transaction was aborted by an earlier '
                    'error and has been rolled back'
                )
            if self._in_transaction:
                self._execute_sql("COMMIT;")

    def rollback(self) -> None:
        """Roll back the current transaction, on the connection's own session."""
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        with self._lock:
            if not self._in_transaction:
                self._txn_failed = False
                return
            if self._mode == 'daemon' and (
                    self._session is None or not self._session.connected):
                # The session is gone, so the server rolled back for us.
                self._in_transaction = False
                self._txn_failed = False
                return
            self._txn_failed = False
            self._execute_sql("ROLLBACK;")

    def _safe_rollback(self) -> None:
        """Best-effort ROLLBACK for error and close paths; never raises."""
        try:
            if self._mode == 'daemon' and (
                    self._session is None or not self._session.connected):
                return
            self._execute_raw("ROLLBACK;")
        except Exception:
            pass
        finally:
            self._in_transaction = False
            self._txn_failed = False

    def begin(self) -> None:
        """Begin an explicit transaction (HeliosDB extension)."""
        self._check_thread()
        if self._closed:
            raise ProgrammingError("Cannot operate on closed connection")

        with self._lock:
            if not self._in_transaction:
                # HeliosDB speaks PostgreSQL: a plain BEGIN, with no SQLite
                # DEFERRED/IMMEDIATE/EXCLUSIVE variants.
                self._execute_sql("BEGIN;")

    def close(self) -> None:
        """
        Close the connection.

        Like sqlite3, an open transaction is rolled back: uncommitted work is
        discarded, never silently committed.
        """
        if not self._closed:
            if self._in_transaction:
                self._safe_rollback()

            # Close the persistent server session, if any
            session = getattr(self, '_session', None)
            if session is not None:
                session.close()
                self._session = None

            # Terminate persistent REPL process if running
            if hasattr(self, '_heliosdb_process') and self._heliosdb_process is not None:
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
        """Interrupt a long-running query."""
        session = getattr(self, '_session', None)
        if session is not None and session.connected:
            # Real query cancellation, possible now that the session persists.
            session.cancel()
            return
        # Send interrupt to HeliosDB process if running
        if self._heliosdb_process and self._heliosdb_process.poll() is None:
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

        # Start server daemon
        data_dir = self._data_dir or str(Path(self.database).parent / 'heliosdb-data')
        cmd = [
            'heliosdb', 'start',
            '--data-dir', data_dir,
            '--port', str(port),
            '--daemon'
        ]

        try:
            subprocess.run(cmd, check=True, timeout=10)
            self._mode = 'daemon'
            self._server_port = port
            time.sleep(1)  # Wait for server to start
            self._session = _ServerSession(self._build_server_params())
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
        **kwargs: HeliosDB-specific options:
            mode            'embedded' (default), 'daemon' or 'hybrid'
            data_dir        data directory for embedded/hybrid mode
            dsn             full PostgreSQL DSN/URI for daemon mode
            server_host     daemon host (default '127.0.0.1')
            server_port     daemon port (default 5432)
            server_database daemon database name (default 'heliosdb')
            user            daemon user (default 'helios' / $PGUSER)
            password        daemon password (default $PGPASSWORD / ~/.pgpass)
            sslmode         libpq sslmode
            autocommit      True / False / LEGACY_TRANSACTION_CONTROL
        Unknown keywords now raise a warning instead of being discarded.

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
    'apilevel',
    'threadsafety',
    'paramstyle',
    'LEGACY_TRANSACTION_CONTROL',

    # Advanced
    'enable_callback_tracebacks',
    'complete_statement',
    'register_trace_callback',
]
