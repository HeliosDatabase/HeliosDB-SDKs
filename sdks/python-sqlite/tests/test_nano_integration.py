"""Integration tests against a running HeliosDB Nano server (daemon mode).

Skipped unless HELIOSDB_TEST_URL is set to a libpq URI such as
``postgresql://postgres@127.0.0.1:5432/heliosdb``. Give the password in the
URI, through libpq (PGPASSWORD, ~/.pgpass), or by naming a file that holds it
in HELIOSDB_TEST_PASSWORD_FILE. scripts/nano-integration-test.sh starts a
throwaway server in Docker and runs this file against it.
"""

import datetime
import os
import uuid

import pytest

import heliosdb_sqlite

pytest.importorskip('psycopg2')

TEST_URL = os.environ.get('HELIOSDB_TEST_URL')

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_URL, reason='HELIOSDB_TEST_URL is not set'),
]


def _password():
    path = os.environ.get('HELIOSDB_TEST_PASSWORD_FILE')
    if not path:
        return None
    with open(path, encoding='utf-8') as fh:
        return fh.readline().rstrip('\r\n')


def connect(**kwargs):
    kwargs.setdefault('isolation_level', None)
    password = _password()
    if password is not None:
        kwargs['server_password'] = password
    return heliosdb_sqlite.connect(':memory:', mode='daemon', dsn=TEST_URL, **kwargs)


@pytest.fixture
def conn():
    c = connect()
    yield c
    c.close()


@pytest.fixture
def table(conn):
    """A unique table name, dropped after the test."""
    names = []

    def make(prefix='t'):
        name = f'{prefix}_{uuid.uuid4().hex[:12]}'
        names.append(name)
        return name

    yield make
    for name in names:
        try:
            conn.execute(f'DROP TABLE IF EXISTS {name}')
        except heliosdb_sqlite.Error:
            pass


def test_integer_column_comes_back_as_int(conn, table):
    t = table()
    conn.execute(f'CREATE TABLE {t} (id INTEGER, code TEXT)')
    conn.execute(f'INSERT INTO {t} VALUES (?, ?)', (7, 'A2'))
    rows = conn.execute(f'SELECT id, code FROM {t}').fetchall()
    assert rows == [(7, 'A2')]
    assert type(rows[0][0]) is int and type(rows[0][1]) is str


def test_column_types(conn, table):
    t = table()
    conn.execute(f'''CREATE TABLE {t} (
        i2 SMALLINT, i4 INTEGER, i8 BIGINT,
        f4 REAL, f8 DOUBLE PRECISION, n NUMERIC(10,2), nw NUMERIC(10,2),
        b BOOLEAN, tx TEXT, vc VARCHAR(10), digits TEXT, nulltext TEXT,
        by BYTEA, d DATE, ts TIMESTAMP, u UUID, j JSON
    )''')
    conn.execute(
        f'INSERT INTO {t} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (-32768, 7, 9000000000, 1.5, 2.25, 3.14, 3, True, 'A2', 'v', '7', 'NULL',
         b'\x00\xffab', '2026-01-02', '2026-01-02 03:04:05',
         'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '{"a": 1}'),
    )
    conn.execute(f'INSERT INTO {t} (i4) VALUES (NULL)')
    cur = conn.execute(f'SELECT * FROM {t}')
    rows = cur.fetchall()
    assert len(rows) == 2
    row = next(r for r in rows if r[1] is not None)
    null_row = next(r for r in rows if r[1] is None)

    expected = {
        'i2': (int, -32768), 'i4': (int, 7), 'i8': (int, 9000000000),
        'f4': (float, 1.5), 'f8': (float, 2.25),
        'n': (float, 3.14), 'nw': (int, 3),
        'b': (int, 1),
        'tx': (str, 'A2'), 'vc': (str, 'v'), 'digits': (str, '7'), 'nulltext': (str, 'NULL'),
        'by': (bytes, b'\x00\xffab'),
        'u': (str, 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'),
    }
    names = [d[0] for d in cur.description]
    got = dict(zip(names, row))
    for name, (typ, value) in expected.items():
        assert type(got[name]) is typ, (name, got[name])
        assert got[name] == value, name
    # DATE / TIMESTAMP / JSON are text, as in sqlite3 (without converters)
    assert type(got['d']) is str and got['d'].startswith('2026-01-02')
    assert type(got['ts']) is str and got['ts'].startswith('2026-01-02 03:04:05')
    assert type(got['j']) is str and '"a"' in got['j']
    assert null_row == (None,) * len(names)


def test_boolean_false_and_empty_bytea(conn, table):
    t = table()
    conn.execute(f'CREATE TABLE {t} (b BOOLEAN, data BYTEA)')
    conn.execute(f'INSERT INTO {t} VALUES (?, ?)', (False, b''))
    assert conn.execute(f'SELECT b, data FROM {t}').fetchone() == (0, b'')


def test_expression_types(conn):
    row = conn.execute(
        "SELECT 7 AS a, 'A2' AS b, CAST(1.5 AS DOUBLE PRECISION) AS c, 10 / 4 AS d, "
        "true AS e, NULL AS f, CAST(2 AS BIGINT) AS g, CAST(1 AS REAL) AS h"
    ).fetchone()
    assert row == (7, 'A2', 1.5, 2, 1, None, 2, 1.0)
    assert [type(v) for v in row] == [int, str, float, int, int, type(None), int, float]


def test_aggregates(conn, table):
    t = table()
    conn.execute(f'CREATE TABLE {t} (x INTEGER)')
    conn.executemany(f'INSERT INTO {t} VALUES (?)', [(1,), (2,), (4,)])
    count, total = conn.execute(f'SELECT count(*), sum(x) FROM {t}').fetchone()
    assert (count, total) == (3, 7)
    assert type(count) is int and type(total) is int


def test_row_factory(conn, table):
    t = table()
    conn.execute(f'CREATE TABLE {t} (id INTEGER, code TEXT)')
    conn.execute(f"INSERT INTO {t} VALUES (7, 'A2')")
    conn.row_factory = heliosdb_sqlite.Row
    row = conn.execute(f'SELECT id, code FROM {t}').fetchone()
    assert row['id'] == 7 and row['code'] == 'A2'


def test_lastrowid(conn, table):
    t = table()
    conn.execute(f'CREATE TABLE {t} (id SERIAL PRIMARY KEY, name TEXT)')
    cur = conn.execute(f'INSERT INTO {t} (name) VALUES (?)', ('a',))
    first = cur.lastrowid
    cur.execute(f'INSERT INTO {t} (name) VALUES (?)', ('b',))
    assert type(first) is int and cur.lastrowid == first + 1


def test_detect_types_converters(table):
    from heliosdb_sqlite import main
    saved = dict(main._converters)
    heliosdb_sqlite.register_converter('DATE', lambda b: datetime.date.fromisoformat(b.decode()[:10]))
    try:
        c = connect(detect_types=heliosdb_sqlite.PARSE_DECLTYPES | heliosdb_sqlite.PARSE_COLNAMES)
        try:
            t = f't_{uuid.uuid4().hex[:12]}'
            c.execute(f'CREATE TABLE {t} (d DATE, s TEXT)')
            try:
                c.execute(f"INSERT INTO {t} VALUES ('2026-01-02', '2026-03-04')")
                cur = c.execute(f'SELECT d, s AS "s [date]" FROM {t}')
                assert cur.fetchone() == (datetime.date(2026, 1, 2), datetime.date(2026, 3, 4))
                assert [d[0] for d in cur.description] == ['d', 's']
            finally:
                c.execute(f'DROP TABLE IF EXISTS {t}')
        finally:
            c.close()
    finally:
        main._converters.clear()
        main._converters.update(saved)


def test_transaction_uses_one_session():
    setup = connect()
    name = f't_{uuid.uuid4().hex[:12]}'
    setup.execute(f'CREATE TABLE {name} (id INTEGER)')
    try:
        writer = connect(isolation_level='DEFERRED')  # BEGIN on connect, as before
        reader = connect()
        try:
            writer.execute(f'INSERT INTO {name} VALUES (1)')
            assert reader.execute(f'SELECT count(*) FROM {name}').fetchone() == (0,)
            writer.rollback()
            assert reader.execute(f'SELECT count(*) FROM {name}').fetchone() == (0,)
            writer.begin()
            writer.execute(f'INSERT INTO {name} VALUES (2)')
            writer.commit()
            assert reader.execute(f'SELECT id FROM {name}').fetchall() == [(2,)]
        finally:
            writer.close()
            reader.close()
    finally:
        setup.execute(f'DROP TABLE IF EXISTS {name}')
        setup.close()


def test_errors_raise_sqlite3_exceptions(conn):
    with pytest.raises(heliosdb_sqlite.DatabaseError):
        conn.execute('SELECT * FROM table_that_does_not_exist_x')


def test_bad_password_is_operational_error():
    try:
        c = heliosdb_sqlite.connect(':memory:', mode='daemon', dsn=TEST_URL,
                                    server_password='not-the-password-' + uuid.uuid4().hex)
    except heliosdb_sqlite.OperationalError:
        return
    c.close()
    pytest.skip('the server accepted a wrong password (trust authentication)')
