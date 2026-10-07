"""Conformance: heliosdb_sqlite against CPython's sqlite3, statement by statement.

Every test runs the same statements on the standard library ``sqlite3``
(in-memory) and on ``heliosdb_sqlite``, and requires the same results with the
same Python types: ``int``, ``float``, ``str``, ``bytes``, ``None``.

Backends:

* ``embedded`` -- the in-process HeliosDB Nano binding
  (``pip install heliosdb-nano-embedded``); skipped when it is not installed.
* ``daemon`` -- a HeliosDB Nano server over the PostgreSQL wire protocol;
  skipped unless HELIOSDB_TEST_URL is set (see tests/test_nano_integration.py
  and scripts/nano-integration-test.sh).
"""

import datetime
import os
import sqlite3
import uuid

import pytest

import heliosdb_sqlite

pytestmark = pytest.mark.compatibility

TEST_URL = os.environ.get('HELIOSDB_TEST_URL')


def _binding_installed():
    try:
        import heliosdb_nano  # noqa: F401
    except Exception:
        return False
    return True


def _password():
    path = os.environ.get('HELIOSDB_TEST_PASSWORD_FILE')
    if not path:
        return None
    with open(path, encoding='utf-8') as fh:
        return fh.readline().rstrip('\r\n')


def _open(backend, **kwargs):
    if backend == 'embedded':
        return heliosdb_sqlite.connect(':memory:', embedded_backend='binding', **kwargs)
    password = _password()
    if password is not None:
        kwargs['server_password'] = password
    return heliosdb_sqlite.connect(':memory:', mode='daemon', dsn=TEST_URL, **kwargs)


@pytest.fixture(params=['embedded', 'daemon'])
def backend(request):
    if request.param == 'embedded' and not _binding_installed():
        pytest.skip('heliosdb-nano-embedded is not installed')
    if request.param == 'daemon':
        if not TEST_URL:
            pytest.skip('HELIOSDB_TEST_URL is not set')
        pytest.importorskip('psycopg2')
    return request.param


class Pair:
    """The same database work on sqlite3 and on heliosdb_sqlite."""

    def __init__(self, backend, **kwargs):
        self.backend = backend
        self.ref = sqlite3.connect(':memory:', **kwargs)
        self.hdb = _open(backend, **kwargs)
        self.tables = []

    def table(self, prefix='t'):
        name = f'{prefix}_{uuid.uuid4().hex[:10]}'
        self.tables.append(name)
        return name

    def both(self, sql, params=()):
        """Run on both; return (sqlite3 cursor, heliosdb_sqlite cursor)."""
        return self.ref.execute(sql, params), self.hdb.execute(sql, params)

    def same_rows(self, sql, params=()):
        ref, hdb = self.both(sql, params)
        expected, got = ref.fetchall(), hdb.fetchall()
        assert_same(expected, got)
        return got

    def close(self):
        try:
            if self.hdb.in_transaction:
                self.hdb.rollback()
            for name in self.tables:
                try:
                    self.hdb.execute(f'DROP TABLE IF EXISTS {name}')
                except heliosdb_sqlite.Error:
                    pass
            if self.hdb.in_transaction:
                self.hdb.commit()
        finally:
            self.hdb.close()
            self.ref.close()


def assert_same(expected, got):
    """Rows equal value for value, and every value has the same type."""
    assert len(got) == len(expected), (expected, got)
    for exp_row, got_row in zip(expected, got):
        assert tuple(got_row) == tuple(exp_row), (exp_row, got_row)
        assert [type(v) for v in got_row] == [type(v) for v in exp_row], (
            [type(v).__name__ for v in exp_row], [type(v).__name__ for v in got_row])


@pytest.fixture
def pair(backend):
    p = Pair(backend)
    yield p
    p.close()


# --------------------------------------------------------------------------
# Storage classes
# --------------------------------------------------------------------------

def test_storage_classes_and_nulls(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, i INTEGER, r REAL, '
              f's TEXT, b BLOB, n NUMERIC)')
    rows = [
        (1, 7, 2.5, 'A2', b'\x00\xff\x10', 3),
        (2, -1, -0.25, '', b'', 2.5),
        (3, None, None, None, None, None),
    ]
    for row in rows:
        pair.both(f'INSERT INTO {t} VALUES (?, ?, ?, ?, ?, ?)', row)
    got = pair.same_rows(f'SELECT id, i, r, s, b, n FROM {t} ORDER BY id')
    assert got[0] == (1, 7, 2.5, 'A2', b'\x00\xff\x10', 3)


def test_the_reported_bug_integer_column_is_int(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER, code TEXT)')
    pair.both(f'INSERT INTO {t} VALUES (?, ?)', (7, 'A2'))
    assert pair.same_rows(f'SELECT id, code FROM {t}') == [(7, 'A2')]


@pytest.mark.parametrize('value', [
    0, 1, -1, 2 ** 31 - 1, 2 ** 31, -(2 ** 31) - 1, 2 ** 53 + 1, 2 ** 62, 2 ** 63 - 1, -(2 ** 63),
])
def test_64_bit_integers(pair, value):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, v INTEGER, w BIGINT)')
    pair.both(f'INSERT INTO {t} (id, v, w) VALUES (?, ?, ?)', (1, value, value))
    assert pair.same_rows(f'SELECT v, w FROM {t}') == [(value, value)]


def test_int_too_large_is_overflow_error(pair):
    with pytest.raises(OverflowError):
        pair.ref.execute('SELECT ?', (2 ** 63,))
    with pytest.raises(OverflowError):
        pair.hdb.execute('SELECT ?', (2 ** 63,))


@pytest.mark.parametrize('value', [
    0.1, 2.5, -0.0, 1.0, 1e-7, 1e300, -1.5e-300, 3.141592653589793, 1 / 3,
])
def test_real_is_a_double(pair, value):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (r REAL, f FLOAT, d DOUBLE)')
    pair.both(f'INSERT INTO {t} VALUES (?, ?, ?)', (value, value, value))
    assert pair.same_rows(f'SELECT r, f, d FROM {t}') == [(value, value, value)]


@pytest.mark.parametrize('text', [
    'plain', '', 'é', '中文字符', '😀 emoji', "it's", 'a "quoted" word', 'line\nbreak',
    'tab\there', 'back\\slash', '?', ':name', '%s', '7', '007', 'NULL', '1e3', ' padded ',
])
def test_text_round_trip(pair, text):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (s TEXT, v VARCHAR(100))')
    pair.both(f'INSERT INTO {t} VALUES (?, ?)', (text, text))
    assert pair.same_rows(f'SELECT s, v FROM {t}') == [(text, text)]


@pytest.mark.parametrize('blob', [
    b'', b'\x00', b'\x00\x01\x02', bytes(range(256)), b"'; DROP TABLE x; --", b'\\x41',
])
def test_blob_round_trip(pair, blob):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (b BLOB)')
    pair.both(f'INSERT INTO {t} VALUES (?)', (blob,))
    pair.both(f'INSERT INTO {t} VALUES (?)', (bytearray(blob),))
    pair.both(f'INSERT INTO {t} VALUES (?)', (memoryview(blob),))
    assert pair.same_rows(f'SELECT b FROM {t}') == [(blob,)] * 3


def test_bool_parameters_bind_as_integers(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (f INTEGER, g INTEGER)')
    pair.both(f'INSERT INTO {t} VALUES (?, ?)', (True, False))
    assert pair.same_rows(f'SELECT f, g FROM {t}') == [(1, 0)]


def test_numeric_affinity(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER, n NUMERIC, d DECIMAL(10, 2))')
    pair.both(f'INSERT INTO {t} VALUES (1, 5, 3.10)')
    pair.both(f"INSERT INTO {t} VALUES (2, 2.5, '9.99')")
    pair.both(f'INSERT INTO {t} VALUES (3, ?, ?)', (-7, 0.5))
    pair.same_rows(f'SELECT n, d FROM {t} ORDER BY id')


def test_expressions(pair):
    pair.same_rows("SELECT 1, 2.5, 'x', NULL, 1 + 1, 10 - 2.5, -3")


def test_parameters_in_expressions(pair):
    pair.same_rows('SELECT ?, ?, ?, ?, ?', (1, 2.5, 'x', None, b'\x01'))


# --------------------------------------------------------------------------
# Cursor surface
# --------------------------------------------------------------------------

def test_description_rowcount_lastrowid(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, name TEXT, score INTEGER)')
    for name in ('a', 'b', 'c'):
        ref, hdb = pair.both(f'INSERT INTO {t} (name, score) VALUES (?, ?)', (name, 1))
        assert hdb.lastrowid == ref.lastrowid
        assert hdb.rowcount == ref.rowcount == 1
        assert hdb.description is ref.description is None
    ref, hdb = pair.both(f'UPDATE {t} SET score = score + 1 WHERE id >= ?', (2,))
    assert hdb.rowcount == ref.rowcount == 2
    ref, hdb = pair.both(f'SELECT id, name AS label, score FROM {t} ORDER BY id')
    assert [d[0] for d in hdb.description] == [d[0] for d in ref.description]
    assert all(len(d) == 7 for d in hdb.description)
    assert hdb.rowcount == ref.rowcount == -1
    assert_same(ref.fetchall(), hdb.fetchall())
    ref, hdb = pair.both(f'SELECT id, name FROM {t} WHERE id < 0')
    assert [d[0] for d in hdb.description] == [d[0] for d in ref.description]
    assert hdb.fetchall() == ref.fetchall() == []
    ref, hdb = pair.both(f'DELETE FROM {t} WHERE id = ?', (1,))
    assert hdb.rowcount == ref.rowcount == 1


def test_fetch_methods(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (n INTEGER)')
    for n in range(5):
        pair.both(f'INSERT INTO {t} VALUES (?)', (n,))
    ref, hdb = pair.both(f'SELECT n FROM {t} ORDER BY n')
    assert hdb.fetchone() == ref.fetchone()
    assert hdb.fetchmany(2) == ref.fetchmany(2)
    assert list(hdb) == list(ref)
    assert hdb.fetchone() is ref.fetchone() is None


def test_executemany(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, v TEXT)')
    rows = [('x',), ('y',), ('z',)]
    ref = pair.ref.executemany(f'INSERT INTO {t} (v) VALUES (?)', rows)
    hdb = pair.hdb.executemany(f'INSERT INTO {t} (v) VALUES (?)', rows)
    assert hdb.rowcount == ref.rowcount == 3
    pair.same_rows(f'SELECT id, v FROM {t} ORDER BY id')
    named = [{'v': 'p'}, {'v': 'q'}]
    ref = pair.ref.executemany(f'UPDATE {t} SET v = :v WHERE id = 1', named)
    hdb = pair.hdb.executemany(f'UPDATE {t} SET v = :v WHERE id = 1', named)
    assert hdb.rowcount == ref.rowcount == 2
    with pytest.raises(sqlite3.ProgrammingError):
        pair.ref.executemany('SELECT ?', [(1,)])
    with pytest.raises(heliosdb_sqlite.ProgrammingError):
        pair.hdb.executemany('SELECT ?', [(1,)])


def test_named_and_numbered_parameters(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (a INTEGER, b TEXT)')
    pair.both(f'INSERT INTO {t} VALUES (:a, :b)', {'a': 1, 'b': 'x'})
    pair.both(f'INSERT INTO {t} VALUES (@a, $b)', {'a': 2, 'b': 'y'})
    pair.both(f'INSERT INTO {t} VALUES (?2, ?1)', ('z', 3))
    pair.same_rows(f'SELECT a, b FROM {t} WHERE b <> :skip ORDER BY a', {'skip': '-'})


def test_wrong_parameter_count_is_programming_error(pair):
    for conn, exc in ((pair.ref, sqlite3.ProgrammingError),
                      (pair.hdb, heliosdb_sqlite.ProgrammingError)):
        with pytest.raises(exc):
            conn.execute('SELECT ?, ?', (1,))
        with pytest.raises(exc):
            conn.execute('SELECT :a', {'b': 1})


def test_row_factory(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER, name TEXT)')
    pair.both(f"INSERT INTO {t} VALUES (1, 'n')")
    pair.ref.row_factory = sqlite3.Row
    pair.hdb.row_factory = heliosdb_sqlite.Row
    ref, hdb = pair.both(f'SELECT id, name FROM {t}')
    r, h = ref.fetchone(), hdb.fetchone()
    assert h.keys() == r.keys()
    assert h['id'] == r['id'] == 1 and h['NAME'] == r['NAME'] == 'n'
    assert tuple(h) == tuple(r) and h[1] == r[1]
    pair.ref.row_factory = pair.hdb.row_factory = lambda cur, row: dict(
        zip([d[0] for d in cur.description], row))
    assert pair.hdb.execute(f'SELECT id FROM {t}').fetchone() == \
        pair.ref.execute(f'SELECT id FROM {t}').fetchone() == {'id': 1}


def test_text_factory_bytes(pair):
    pair.ref.text_factory = bytes
    pair.hdb.text_factory = bytes
    pair.same_rows("SELECT 'abc', 1")


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

def test_implicit_transaction_commit_and_rollback(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (v INTEGER)')
    assert pair.hdb.in_transaction == pair.ref.in_transaction
    pair.both(f'INSERT INTO {t} VALUES (1)')
    assert pair.hdb.in_transaction is pair.ref.in_transaction is True
    pair.ref.rollback()
    pair.hdb.rollback()
    assert pair.hdb.in_transaction is pair.ref.in_transaction is False
    pair.same_rows(f'SELECT count(*) FROM {t}')
    pair.both(f'INSERT INTO {t} VALUES (2)')
    pair.ref.commit()
    pair.hdb.commit()
    pair.both(f'INSERT INTO {t} VALUES (3)')
    pair.ref.rollback()
    pair.hdb.rollback()
    assert pair.same_rows(f'SELECT v FROM {t}') == [(2,)]


def test_context_manager(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (v INTEGER)')
    for conn in (pair.ref, pair.hdb):
        with conn:
            conn.execute(f'INSERT INTO {t} VALUES (1)')
        with pytest.raises(ZeroDivisionError):
            with conn:
                conn.execute(f'INSERT INTO {t} VALUES (2)')
                1 / 0
    assert pair.same_rows(f'SELECT v FROM {t}') == [(1,)]


def test_autocommit_isolation_level_none(backend):
    p = Pair(backend, isolation_level=None)
    try:
        t = p.table()
        p.both(f'CREATE TABLE {t} (v INTEGER)')
        p.both(f'INSERT INTO {t} VALUES (1)')
        assert p.hdb.in_transaction is p.ref.in_transaction is False
        p.ref.rollback()
        p.hdb.rollback()
        assert p.same_rows(f'SELECT v FROM {t}') == [(1,)]
        p.both('BEGIN')
        assert p.hdb.in_transaction is p.ref.in_transaction is True
        p.both(f'INSERT INTO {t} VALUES (2)')
        p.both('ROLLBACK')
        assert p.hdb.in_transaction is p.ref.in_transaction is False
        assert p.same_rows(f'SELECT v FROM {t}') == [(1,)]
    finally:
        p.close()


def test_executescript(pair):
    t = pair.table()
    script = (f"CREATE TABLE {t} (id INTEGER, s TEXT);\n"
              f"INSERT INTO {t} VALUES (1, 'semi;colon');\n"
              f"-- a comment; with a semicolon\n"
              f"INSERT INTO {t} VALUES (2, 'it''s');\n")
    pair.ref.executescript(script)
    pair.hdb.executescript(script)
    pair.same_rows(f'SELECT id, s FROM {t} ORDER BY id')


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

def test_integrity_error(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, u TEXT UNIQUE, n TEXT NOT NULL)')
    pair.both(f"INSERT INTO {t} VALUES (1, 'a', 'x')")
    for conn, exc in ((pair.ref, sqlite3.IntegrityError),
                      (pair.hdb, heliosdb_sqlite.IntegrityError)):
        with pytest.raises(exc):
            conn.execute(f"INSERT INTO {t} VALUES (1, 'b', 'x')")
        with pytest.raises(exc):
            conn.execute(f"INSERT INTO {t} VALUES (2, 'a', 'x')")
        with pytest.raises(exc):
            conn.execute(f"INSERT INTO {t} VALUES (3, 'c', NULL)")


def test_syntax_and_missing_table_are_operational_errors(pair):
    for conn, exc in ((pair.ref, sqlite3.OperationalError),
                      (pair.hdb, heliosdb_sqlite.OperationalError)):
        with pytest.raises(exc):
            conn.execute('SELEC 1')
        with pytest.raises(exc):
            conn.execute('SELECT * FROM no_such_table_' + uuid.uuid4().hex[:8])


# --------------------------------------------------------------------------
# Adapters and converters
# --------------------------------------------------------------------------

def test_date_and_datetime_parameters(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (d TEXT, ts TEXT)')
    value = (datetime.date(2026, 10, 7), datetime.datetime(2026, 10, 7, 12, 34, 56))
    pair.both(f'INSERT INTO {t} VALUES (?, ?)', tuple(v.isoformat(' ') if isinstance(
        v, datetime.datetime) else v.isoformat() for v in value))
    pair.same_rows(f'SELECT d, ts FROM {t}')


def test_detect_types_decltypes(backend):
    p = Pair(backend, detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        t = p.table()
        p.both(f'CREATE TABLE {t} (d DATE, ts TIMESTAMP)')
        stamp = '2026-10-07 12:34:56.5'
        p.both(f'INSERT INTO {t} VALUES (?, ?)', ('2026-10-07', stamp))
        got = p.same_rows(f'SELECT d, ts FROM {t}')
        assert got == [(datetime.date(2026, 10, 7), datetime.datetime(2026, 10, 7, 12, 34, 56, 500000))]
    finally:
        p.close()


def test_register_adapter(pair):
    class Point:
        def __init__(self, x, y):
            self.x, self.y = x, y

    def adapt(p):
        return f'{p.x};{p.y}'

    sqlite3.register_adapter(Point, adapt)
    heliosdb_sqlite.register_adapter(Point, adapt)
    try:
        pair.same_rows('SELECT ?', (Point(1, 2),))
    finally:
        sqlite3.adapters.pop((Point, sqlite3.PrepareProtocol), None)
        from heliosdb_sqlite import main
        main._adapters.pop(Point, None)
