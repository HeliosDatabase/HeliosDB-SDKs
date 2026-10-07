"""Unit tests for result-value typing (heliosdb_sqlite._types and the cursor).

Values arrive in the PostgreSQL wire protocol's text format together with the
column type OIDs from RowDescription; they must come back as the Python types
the standard library sqlite3 module returns. No server is needed: the cursor
tests use an in-process fake transport, and the daemon-session tests install
a fake psycopg2.
"""

import datetime
import math
import sys
import types
from decimal import Decimal

import pytest

import heliosdb_sqlite
from heliosdb_sqlite import _types
from heliosdb_sqlite._types import (
    BOOL, BPCHAR, BYTEA, DATE, FLOAT4, FLOAT8, INT2, INT4, INT8, JSON, JSONB,
    NUMERIC, TEXT, TIMESTAMP, TIMESTAMPTZ, UNKNOWN, UUID, VARCHAR,
    convert_row, convert_value,
)
from heliosdb_sqlite.main import Connection


# --------------------------------------------------------------------------
# convert_value: one column value, by type OID
# --------------------------------------------------------------------------

@pytest.mark.parametrize('oid,text,expected', [
    (INT2, '-32768', -32768),
    (INT4, '7', 7),
    (INT4, '0', 0),
    (INT8, '9223372036854775807', 9223372036854775807),
    (INT8, '-9000000000', -9000000000),
])
def test_integers(oid, text, expected):
    value = convert_value(oid, text)
    assert value == expected and type(value) is int


@pytest.mark.parametrize('oid,text,expected', [
    (FLOAT4, '1.5', 1.5),
    (FLOAT4, '0.1', 0.1),
    (FLOAT8, '2.25', 2.25),
    (FLOAT8, '1e-7', 1e-7),
    (FLOAT8, '-0', -0.0),
    (FLOAT8, '1.0', 1.0),  # stays float, as sqlite3 returns REAL 1.0
    (FLOAT8, 'inf', math.inf),  # HeliosDB Nano's spelling
    (FLOAT8, 'Infinity', math.inf),  # PostgreSQL's spelling
    (FLOAT4, '-inf', -math.inf),
    (FLOAT4, '-Infinity', -math.inf),
])
def test_floats(oid, text, expected):
    value = convert_value(oid, text)
    assert value == expected and type(value) is float


def test_float_nan():
    assert math.isnan(convert_value(FLOAT8, 'NaN'))


@pytest.mark.parametrize('text,expected', [
    ('3', 3),
    ('3.00', 3),  # integral -> int, like SQLite NUMERIC affinity
    ('-42', -42),
    ('0.000', 0),
    ('9223372036854775807', 9223372036854775807),
])
def test_numeric_integral_is_int(text, expected):
    value = convert_value(NUMERIC, text)
    assert value == expected and type(value) is int


@pytest.mark.parametrize('text,expected', [
    ('3.14', 3.14),
    ('-0.5', -0.5),
    ('12345678901234567000', 1.2345678901234567e19),  # beyond int64 -> REAL
    ('Infinity', math.inf),
])
def test_numeric_fractional_or_huge_is_float(text, expected):
    value = convert_value(NUMERIC, text)
    assert value == expected and type(value) is float


def test_numeric_nan():
    assert math.isnan(convert_value(NUMERIC, 'NaN'))


@pytest.mark.parametrize('text,expected', [
    ('t', 1), ('f', 0), ('true', 1), ('false', 0), ('TRUE', 1), ('FALSE', 0),
])
def test_bool_is_int_like_sqlite3(text, expected):
    value = convert_value(BOOL, text)
    assert value == expected and type(value) is int


@pytest.mark.parametrize('text,expected', [
    ('\\xdeadbeef', b'\xde\xad\xbe\xef'),
    ('\\x00ff', b'\x00\xff'),
    ('\\x', b''),
    ('\\xDEADBEEF', b'\xde\xad\xbe\xef'),
    # bytea_output = escape
    ('abc', b'abc'),
    ('a\\\\b', b'a\\b'),
    ('\\000\\377x', b'\x00\xffx'),
])
def test_bytea_is_bytes(text, expected):
    value = convert_value(BYTEA, text)
    assert value == expected and type(value) is bytes


@pytest.mark.parametrize('oid,text', [
    (TEXT, '7'),  # digits in a TEXT column stay text
    (TEXT, 'NULL'),  # the string 'NULL' is not NULL
    (TEXT, ''),
    (VARCHAR, 'A2'),
    (BPCHAR, 'abc  '),  # CHAR(n) padding is kept, as the server sent it
    (DATE, '2026-01-02'),
    (TIMESTAMP, '2026-01-02 03:04:05.000000'),
    (TIMESTAMPTZ, '2026-01-02 03:04:05.000000+00'),
    (UUID, 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'),
    (JSON, '{"a":1}'),
    (JSONB, '{"b": 2}'),
    (UNKNOWN, '{1,2,3}'),  # array literal
    (99999, '[1.0,2.0,3.0]'),  # any type this module does not know
])
def test_text_like_types_are_str(oid, text):
    value = convert_value(oid, text)
    assert value == text and type(value) is str


@pytest.mark.parametrize('oid', [INT2, INT4, INT8, FLOAT4, FLOAT8, NUMERIC, BOOL,
                                 BYTEA, TEXT, VARCHAR, DATE, UUID, JSON, 99999, None])
def test_null_is_none_for_every_type(oid):
    assert convert_value(oid, None) is None


def test_no_type_information_leaves_value_alone():
    assert convert_value(None, '7') == '7'
    assert convert_row(None, ['7', 'A2']) == ['7', 'A2']
    assert convert_row([], ['7', 'A2']) == ['7', 'A2']


def test_values_already_decoded_by_a_driver():
    # A driver that decodes types itself must still end up at sqlite3 types.
    assert convert_value(BOOL, True) == 1 and type(convert_value(BOOL, True)) is int
    assert convert_value(BOOL, False) == 0
    assert convert_value(NUMERIC, Decimal('3.14')) == 3.14
    assert type(convert_value(NUMERIC, Decimal('3.00'))) is int
    assert convert_value(BYTEA, memoryview(b'\x01\x02')) == b'\x01\x02'
    assert type(convert_value(BYTEA, memoryview(b'\x01'))) is bytes
    assert convert_value(INT8, 5) == 5
    assert convert_value(FLOAT8, 2) == 2.0 and type(convert_value(FLOAT8, 2)) is float
    assert convert_value(TEXT, b'caf\xc3\xa9') == 'caf\xe9'


@pytest.mark.parametrize('oid,text', [
    (INT4, 'abc'), (INT4, '7.5'), (FLOAT8, 'x'), (NUMERIC, 'nope'),
    (BOOL, 'maybe'), (BYTEA, '\\xzz'), (BYTEA, '\\12'),
])
def test_invalid_text_for_type_raises(oid, text):
    with pytest.raises(ValueError):
        convert_value(oid, text)


def test_convert_row_uses_each_columns_type():
    assert convert_row([INT4, TEXT], ['7', 'A2']) == [7, 'A2']
    assert convert_row([INT4, TEXT, BOOL, BYTEA], [None, None, 'f', '\\x01']) == [None, None, 0, b'\x01']
    # a short type list leaves the extra columns untouched
    assert convert_row([INT4], ['1', '2']) == [1, '2']


# --------------------------------------------------------------------------
# Cursor: rows from a transport, with and without type OIDs
# --------------------------------------------------------------------------

def make_conn(responses, **kwargs):
    """A Connection whose transport returns canned results (no engine)."""
    sent = []

    class _Conn(Connection):
        def _initialize_heliosdb(self):
            self._heliosdb_process = None

        def _execute_sql(self, sql):
            sent.append(sql)
            for prefix, result in responses:
                if sql.strip().upper().startswith(prefix):
                    return result
            return 0

    kwargs.setdefault('isolation_level', None)
    return _Conn(':memory:', **kwargs), sent


WIRE_RESULT = {
    'columns': ['id', 'code'],
    'types': [INT4, TEXT],
    'rows': [['7', 'A2'], ['8', '9'], [None, 'NULL']],
}

REPL_RESULT = {  # the embedded REPL transport has no type information
    'columns': ['id', 'code'],
    'rows': [['7', 'A2']],
}


def test_cursor_converts_by_type_oid():
    conn, _ = make_conn([('SELECT', WIRE_RESULT)])
    cur = conn.execute('SELECT id, code FROM t')
    assert cur.fetchone() == (7, 'A2')
    assert cur.fetchall() == [(8, '9'), (None, 'NULL')]
    assert cur.description == [
        ('id', None, None, None, None, None, None),
        ('code', None, None, None, None, None, None),
    ]
    assert cur.rowcount == -1  # sqlite3 reports -1 for SELECT


def test_row_factory_sees_converted_values():
    conn, _ = make_conn([('SELECT', WIRE_RESULT)])
    conn.row_factory = heliosdb_sqlite.Row
    row = conn.execute('SELECT id, code FROM t').fetchone()
    assert row['id'] == 7 and row['code'] == 'A2'
    assert row.keys() == ['id', 'code']


def test_fetchmany_and_iteration_see_converted_values():
    conn, _ = make_conn([('SELECT', WIRE_RESULT)])
    cur = conn.execute('SELECT id, code FROM t')
    assert cur.fetchmany(2) == [(7, 'A2'), (8, '9')]
    conn2, _ = make_conn([('SELECT', WIRE_RESULT)])
    assert list(conn2.execute('SELECT id, code FROM t')) == [(7, 'A2'), (8, '9'), (None, 'NULL')]


def test_untyped_transport_rows_unchanged():
    conn, _ = make_conn([('SELECT', REPL_RESULT)])
    assert conn.execute('SELECT id, code FROM t').fetchall() == [('7', 'A2')]


def test_lastrowid_from_typed_returning():
    pragma = {
        'columns': ['cid', 'name', 'type', 'notnull', 'dflt_value', 'pk'],
        'types': [INT4, TEXT, TEXT, INT4, TEXT, INT4],
        'rows': [['0', 'id', 'INT4', '1', None, '1'], ['1', 'name', 'TEXT', '0', None, '0']],
    }
    returning = {'columns': ['id'], 'types': [INT4], 'rows': [['41'], ['42']]}
    conn, sent = make_conn([('PRAGMA', pragma), ('INSERT', returning)])
    cur = conn.execute("INSERT INTO users (name) VALUES ('a'), ('b')")
    assert cur.lastrowid == 42 and type(cur.lastrowid) is int
    assert cur.rowcount == 2
    assert sent[-1].endswith('RETURNING "id"')


@pytest.fixture
def date_converter():
    from heliosdb_sqlite import main
    saved = dict(main._converters)
    heliosdb_sqlite.register_converter('date', lambda b: datetime.date.fromisoformat(b.decode()))
    heliosdb_sqlite.register_converter('blob', lambda b: ('blob', b))
    yield
    main._converters.clear()
    main._converters.update(saved)


DATE_RESULT = {
    'columns': ['d', 'raw', 'n'],
    'types': [DATE, BYTEA, INT4],
    'rows': [['2026-01-02', '\\x00ff', '5'], [None, None, None]],
}


def test_parse_decltypes_uses_type_oid(date_converter):
    conn, _ = make_conn([('SELECT', DATE_RESULT)], detect_types=heliosdb_sqlite.PARSE_DECLTYPES)
    rows = conn.execute('SELECT d, raw, n FROM t').fetchall()
    # converters get bytes (decoded BYTEA for BLOB), never NULL
    assert rows == [(datetime.date(2026, 1, 2), ('blob', b'\x00\xff'), 5), (None, None, None)]


def test_without_detect_types_converters_are_not_used(date_converter):
    conn, _ = make_conn([('SELECT', DATE_RESULT)])
    assert conn.execute('SELECT d, raw, n FROM t').fetchone() == ('2026-01-02', b'\x00\xff', 5)


def test_parse_colnames(date_converter):
    result = {'columns': ['d [date]', 'n'], 'types': [TEXT, INT4], 'rows': [['2026-01-02', '5']]}
    conn, _ = make_conn([('SELECT', result)], detect_types=heliosdb_sqlite.PARSE_COLNAMES)
    cur = conn.execute('SELECT d AS "d [date]", n FROM t')
    assert cur.fetchone() == (datetime.date(2026, 1, 2), 5)
    assert [d[0] for d in cur.description] == ['d', 'n']


def test_parse_colnames_also_works_without_type_oids(date_converter):
    result = {'columns': ['d [date]'], 'rows': [['2026-01-02']]}
    conn, _ = make_conn([('SELECT', result)], detect_types=heliosdb_sqlite.PARSE_COLNAMES)
    assert conn.execute('SELECT 1').fetchone() == (datetime.date(2026, 1, 2),)


def test_bytes_parameters_use_bytea_hex_literal():
    conn, sent = make_conn([])
    conn.execute('INSERT INTO t (data) VALUES (?)', (b'\x00\xffab',))
    conn.execute('INSERT INTO t (data) VALUES (?)', (bytearray(b'\x01'),))
    conn.execute('INSERT INTO t (data) VALUES (?)', (memoryview(b''),))
    assert sent[-3:] == [
        "INSERT INTO t (data) VALUES ('\\x00ff6162'::bytea)",
        "INSERT INTO t (data) VALUES ('\\x01'::bytea)",
        "INSERT INTO t (data) VALUES ('\\x'::bytea)",
    ]


# --------------------------------------------------------------------------
# Daemon mode: one psycopg2 session, text values + type OIDs
# --------------------------------------------------------------------------

class FakePsycopg2:
    """Just enough of psycopg2 for the daemon transport."""

    def __init__(self, results):
        self.results = results  # list of (sql_prefix, description, rows | rowcount)
        self.connections = []
        self.registered = []
        self.Error = type('Error', (Exception,), {})
        self.DatabaseError = type('DatabaseError', (self.Error,), {})
        self.IntegrityError = type('IntegrityError', (self.DatabaseError,), {})
        self.OperationalError = type('OperationalError', (self.DatabaseError,), {})
        fake = self

        class Cursor:
            def __init__(self, conn):
                self.conn = conn
                self.description = None
                self.rowcount = -1
                self._rows = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql):
                self.conn.statements.append(sql)
                for prefix, description, rows in fake.results:
                    if sql.upper().startswith(prefix):
                        if isinstance(rows, Exception):
                            raise rows
                        if description is None:
                            self.description, self.rowcount = None, rows
                        else:
                            self.description, self._rows = description, rows
                            self.rowcount = len(rows)
                        return
                self.description, self.rowcount = None, 0

            def fetchall(self):
                return [tuple(r) for r in self._rows]

        class Conn:
            def __init__(self, dsn, params):
                self.dsn, self.params = dsn, params
                self.autocommit = False
                self.closed = 0
                self.statements = []

            def cursor(self):
                return Cursor(self)

            def close(self):
                self.closed = 1

        self._Conn = Conn
        ext = types.ModuleType('psycopg2.extensions')
        ext.string_types = {16: object(), 20: object(), 23: object(), 1700: object()}
        ext.new_type = lambda oids, name, caster: (oids, name, caster)
        ext.register_type = lambda t, scope: fake.registered.append((t, scope))
        self.extensions = ext

    def connect(self, dsn, **params):
        conn = self._Conn(dsn, params)
        self.connections.append(conn)
        return conn

    def module(self):
        mod = types.ModuleType('psycopg2')
        for name in ('connect', 'extensions', 'Error', 'DatabaseError',
                     'IntegrityError', 'OperationalError'):
            setattr(mod, name, getattr(self, name))
        return mod


@pytest.fixture
def fake_pg(monkeypatch):
    def install(results):
        fake = FakePsycopg2(results)
        monkeypatch.setitem(sys.modules, 'psycopg2', fake.module())
        monkeypatch.setitem(sys.modules, 'psycopg2.extensions', fake.extensions)
        return fake
    return install


def desc(*cols):
    return [(name, oid, None, None, None, None, None) for name, oid in cols]


def test_daemon_rows_converted_from_row_description(fake_pg):
    fake = fake_pg([('SELECT', desc(('id', INT4), ('code', TEXT)), [('7', 'A2')])])
    conn = heliosdb_sqlite.connect('x', mode='daemon', isolation_level=None)
    assert conn.execute('SELECT id, code FROM t').fetchall() == [(7, 'A2')]
    # every type the driver knows is read as text; conversion happens here
    (oids, name, caster), scope = fake.registered[0]
    assert set(oids) == {16, 20, 23, 1700} and scope is fake.connections[0]
    assert caster('t', None) == 't'


def test_daemon_uses_one_autocommit_session(fake_pg):
    fake = fake_pg([('SELECT', desc(('n', INT8)), [('1',)])])
    conn = heliosdb_sqlite.connect('x', mode='daemon')  # isolation_level DEFERRED
    conn.execute('INSERT INTO t VALUES (1)')
    conn.execute('SELECT count(*) FROM t')
    conn.commit()
    assert len(fake.connections) == 1
    pg = fake.connections[0]
    assert pg.autocommit is True
    assert pg.statements[0] == 'BEGIN;' and pg.statements[-1] == 'COMMIT;'
    conn.close()
    assert pg.closed


def test_daemon_close_rolls_back_then_closes(fake_pg):
    fake = fake_pg([])
    conn = heliosdb_sqlite.connect('x', mode='daemon')
    conn.execute('INSERT INTO t VALUES (1)')
    conn.close()
    pg = fake.connections[0]
    assert pg.statements[-1] == 'ROLLBACK;' and pg.closed


def test_daemon_connect_params_defaults(fake_pg):
    fake = fake_pg([])
    heliosdb_sqlite.connect('x', mode='daemon', isolation_level=None, timeout=0.2)
    pg = fake.connections[0]
    assert pg.dsn == ''
    assert pg.params == {'host': '127.0.0.1', 'port': 5432, 'user': 'helios',
                         'dbname': 'heliosdb', 'connect_timeout': 1}
    # no password: leave PGPASSWORD / ~/.pgpass to libpq


def test_daemon_connect_params_with_dsn_and_overrides(fake_pg):
    fake = fake_pg([])
    heliosdb_sqlite.connect('x', mode='daemon', isolation_level=None,
                            dsn='postgresql://app@db.example:6543/appdb',
                            server_password='pw', server_port=7000)
    pg = fake.connections[0]
    assert pg.dsn == 'postgresql://app@db.example:6543/appdb'
    assert pg.params == {'password': 'pw', 'port': 7000, 'connect_timeout': 5}


def test_daemon_driver_errors_map_to_sqlite3_classes(fake_pg):
    fake = fake_pg([])
    fake.results.append(('INSERT', None, fake.IntegrityError('duplicate key')))
    conn = heliosdb_sqlite.connect('x', mode='daemon', isolation_level=None)
    with pytest.raises(heliosdb_sqlite.DatabaseError, match='duplicate key'):
        conn.execute('INSERT INTO t VALUES (1)')
    with pytest.raises(heliosdb_sqlite.IntegrityError):
        conn._execute_sql('INSERT INTO t VALUES (1)')


def test_daemon_connect_failure_is_operational_error(fake_pg):
    fake = fake_pg([])

    def refuse(dsn, **params):
        raise fake.OperationalError('password authentication failed')
    sys.modules['psycopg2'].connect = refuse
    with pytest.raises(heliosdb_sqlite.OperationalError, match='password authentication failed'):
        heliosdb_sqlite.connect('x', mode='daemon')


def test_daemon_lost_connection_mid_transaction_is_reported(fake_pg):
    fake = fake_pg([])
    conn = heliosdb_sqlite.connect('x', mode='daemon')
    conn.begin()
    fake.connections[0].closed = 2  # server went away
    with pytest.raises(heliosdb_sqlite.OperationalError, match='rolled back'):
        conn._execute_sql('SELECT 1')
    # the next statement opens a fresh session
    conn._execute_sql('SELECT 1')
    assert len(fake.connections) == 2


def test_daemon_close_after_lost_connection_does_not_raise(fake_pg):
    fake = fake_pg([])
    conn = heliosdb_sqlite.connect('x', mode='daemon')
    conn.begin()  # BEGIN sent
    fake.connections[0].closed = 2
    conn.close()
    assert len(fake.connections) == 1  # no reconnect just to roll back
    assert fake.connections[0].statements == ['BEGIN;']


def test_daemon_needs_psycopg2(monkeypatch):
    monkeypatch.setitem(sys.modules, 'psycopg2', None)
    with pytest.raises(heliosdb_sqlite.InterfaceError, match='psycopg2'):
        heliosdb_sqlite.connect('x', mode='daemon')


def test_type_names_cover_converted_oids():
    for oid in (INT2, INT4, INT8, FLOAT4, FLOAT8, NUMERIC, BOOL, BYTEA, TEXT, DATE):
        assert _types.TYPE_NAMES[oid]
