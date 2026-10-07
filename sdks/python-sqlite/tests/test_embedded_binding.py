"""Embedded mode on the in-process binding (heliosdb-nano-embedded), plus the
SQL text helpers it relies on. Binding tests are skipped when the wheel is
not installed; the helper and fallback tests always run."""

import os
import warnings

import pytest

import heliosdb_sqlite
from heliosdb_sqlite import _embedded, _sql


def _binding():
    return _embedded.load_binding()


needs_binding = pytest.mark.skipif(_binding() is None,
                                   reason='heliosdb-nano-embedded is not installed')


# --------------------------------------------------------------------------
# SQL helpers
# --------------------------------------------------------------------------

def test_placeholders_skip_strings_comments_and_casts():
    sql = ("SELECT ?, 'not ? here', \"col?\", :name, x::text, @a, $b, ?3 "
           "-- trailing ?\n/* :c */ FROM t")
    found = [(p.kind, p.key) for p in _sql.find_placeholders(sql)]
    assert found == [('qmark', None), ('named', 'name'), ('named', 'a'), ('named', 'b'),
                     ('numbered', 3)]


def test_native_dollar_placeholders():
    assert [(p.kind, p.key) for p in _sql.find_placeholders('SELECT $1, $2')] == [
        ('native', 1), ('native', 2)]


def test_split_statements_respects_quotes_and_comments():
    script = "CREATE TABLE a (s TEXT); INSERT INTO a VALUES ('x;y'); -- c;\n; /* ; */ SELECT 1"
    assert _sql.split_statements(script) == [
        'CREATE TABLE a (s TEXT)', "INSERT INTO a VALUES ('x;y')", '/* ; */ SELECT 1']


def test_first_keyword_and_has_keyword():
    assert _sql.first_keyword('  -- hi\n (SELECT 1)') == 'SELECT'
    assert _sql.has_keyword("INSERT INTO t VALUES (1) returning id", 'RETURNING')
    assert not _sql.has_keyword("INSERT INTO t VALUES ('returning')", 'RETURNING')


@pytest.mark.parametrize('sql,expected', [
    ('CREATE TABLE t (id INTEGER PRIMARY KEY, n INT, r REAL, b BLOB, d DATETIME)',
     "CREATE TABLE t (id BIGINT PRIMARY KEY DEFAULT nextval('t_id_rowid_seq'), n BIGINT, "
     "r DOUBLE PRECISION, b BYTEA, d TIMESTAMP)"),
    ('create table if not exists s.t(id integer primary key autoincrement, x tinyint)',
     "create table if not exists s.t(id BIGINT primary key DEFAULT nextval('t_id_rowid_seq'), "
     "x BIGINT)"),
    # table-level PRIMARY KEY on an INTEGER column is a rowid alias too
    ('CREATE TABLE t (a INTEGER, b TEXT, PRIMARY KEY (a))',
     "CREATE TABLE t (a BIGINT DEFAULT nextval('t_a_rowid_seq'), b TEXT, PRIMARY KEY (a))"),
    # ... but not a composite key, or INTEGER under another name
    ('CREATE TABLE t (a INTEGER, b INT, PRIMARY KEY (a, b))',
     'CREATE TABLE t (a BIGINT, b BIGINT, PRIMARY KEY (a, b))'),
    ('CREATE TABLE t (a BIGINT PRIMARY KEY)', 'CREATE TABLE t (a BIGINT PRIMARY KEY)'),
    # names HeliosDB does not know are mapped by SQLite affinity
    ('CREATE TABLE t (p POINT, m MYTYPE, c NCHAR(5), k MONEY, f FLOATY, bb BOOL, x TEXT[])',
     'CREATE TABLE t (p POINT, m TEXT, c TEXT, k TEXT, f DOUBLE PRECISION, bb BOOLEAN, x TEXT[])'),
    ("CREATE TABLE t (p DECIMAL(10, 2), s TEXT DEFAULT 'REAL, BLOB', v VARCHAR(5), "
     "PRIMARY KEY (s), CHECK (p > 0))",
     "CREATE TABLE t (p DECIMAL(10, 2), s TEXT DEFAULT 'REAL, BLOB', v VARCHAR(5), "
     "PRIMARY KEY (s), CHECK (p > 0))"),
    ('ALTER TABLE t ADD COLUMN z BLOB', 'ALTER TABLE t ADD COLUMN z BYTEA'),
    ('CREATE INDEX i ON t (a)', 'CREATE INDEX i ON t (a)'),
    ('SELECT CAST(1 AS REAL)', 'SELECT CAST(1 AS REAL)'),
])
def test_rewrite_ddl_types(sql, expected):
    assert _sql.rewrite_ddl_types(sql) == expected


def test_referenced_tables():
    assert _sql.referenced_tables(
        'SELECT a.x, b.y FROM alpha a, "Beta" b JOIN gamma g ON g.id = b.id WHERE 1') == [
        'alpha', 'Beta', 'gamma']


def test_timestamp_normalisation():
    assert _embedded._sql_timestamp('2026-10-07T12:00:00.500+00:00', False) == \
        '2026-10-07 12:00:00.5'
    assert _embedded._sql_timestamp('2026-10-07T12:00:00+00:00', True) == \
        '2026-10-07 12:00:00+00'
    assert _embedded._sql_timestamp('not a timestamp', False) == 'not a timestamp'


def test_shortest_float4():
    import struct
    widened = struct.unpack('<f', struct.pack('<f', 0.1))[0]
    assert widened != 0.1 and _embedded._shortest_float4(widened) == 0.1


@pytest.mark.parametrize('message,expected', [
    ('Constraint violation: Duplicate key', 'IntegrityError'),
    ('SQL parse error: Multiple statements found, expected one', 'ProgrammingError'),
    ("Query execution error: Cannot cast 'abc' to INT4", 'DataError'),
    ("Query execution error: Table 'x' does not exist", 'OperationalError'),
])
def test_classify_error(message, expected):
    assert _embedded.classify_error(message) == expected


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------

def test_fallback_warns_when_binding_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(_embedded, 'load_binding', lambda: None)
    monkeypatch.setenv('HELIOSDB_BINARY', str(tmp_path / 'no-such-heliosdb-nano'))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(heliosdb_sqlite.InterfaceError):
            heliosdb_sqlite.connect(':memory:')
    assert any(issubclass(w.category, RuntimeWarning)
               and 'heliosdb-nano-embedded' in str(w.message) for w in caught)


def test_binding_required_raises_when_missing(monkeypatch):
    monkeypatch.setattr(_embedded, 'load_binding', lambda: None)
    with pytest.raises(heliosdb_sqlite.InterfaceError, match='heliosdb-nano-embedded'):
        heliosdb_sqlite.connect(':memory:', embedded_backend='binding')


def test_unknown_backend_rejected():
    with pytest.raises(heliosdb_sqlite.InterfaceError):
        heliosdb_sqlite.connect(':memory:', embedded_backend='bogus')


# --------------------------------------------------------------------------
# In-process binding
# --------------------------------------------------------------------------

@needs_binding
def test_default_embedded_mode_uses_binding_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        conn = heliosdb_sqlite.connect(':memory:')
    try:
        assert conn._backend is not None
        conn.execute('CREATE TABLE t (id INTEGER, code TEXT)')
        conn.execute('INSERT INTO t VALUES (?, ?)', (7, 'A2'))
        assert conn.execute('SELECT id, code FROM t').fetchall() == [(7, 'A2')]
    finally:
        conn.close()


@needs_binding
def test_memory_databases_are_private():
    a = heliosdb_sqlite.connect(':memory:', isolation_level=None)
    b = heliosdb_sqlite.connect(':memory:', isolation_level=None)
    try:
        a.execute('CREATE TABLE only_a (x INTEGER)')
        with pytest.raises(heliosdb_sqlite.OperationalError):
            b.execute('SELECT * FROM only_a')
    finally:
        a.close()
        b.close()


@needs_binding
def test_file_database_persists_and_is_shared(tmp_path):
    data_dir = str(tmp_path / 'db')
    a = heliosdb_sqlite.connect('app.db', data_dir=data_dir)
    b = heliosdb_sqlite.connect('app.db', data_dir=data_dir, timeout=0.3)
    try:
        a.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)')
        a.execute("INSERT INTO t (v) VALUES ('x')")
        # a holds the write transaction: b waits, then reports "locked"
        with pytest.raises(heliosdb_sqlite.OperationalError, match='locked'):
            b.execute('SELECT count(*) FROM t')
        a.commit()
        assert b.execute('SELECT v FROM t').fetchall() == [('x',)]
    finally:
        a.close()
        b.close()
    c = heliosdb_sqlite.connect('app.db', data_dir=data_dir)
    try:
        assert c.execute('SELECT id, v FROM t').fetchall() == [(1, 'x')]
    finally:
        c.close()
    assert os.path.isdir(data_dir)


@needs_binding
def test_pragma_table_info_and_unknown_pragmas():
    conn = heliosdb_sqlite.connect(':memory:')
    try:
        conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL)')
        rows = conn.execute('PRAGMA table_info(t)').fetchall()
        assert [(r[1], r[5]) for r in rows] == [('id', 1), ('name', 0)]
        assert rows[1][3] == 1
        cur = conn.execute('PRAGMA journal_mode = WAL')
        assert cur.fetchall() == [] and cur.description is None
    finally:
        conn.close()


@needs_binding
def test_lastrowid_and_executemany_batch():
    conn = heliosdb_sqlite.connect(':memory:')
    try:
        conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)')
        cur = conn.execute("INSERT INTO t (v) VALUES ('a')")
        assert cur.lastrowid == 1
        cur.executemany('INSERT INTO t (v) VALUES (?)', [('b',), ('c',)])
        assert cur.rowcount == 2 and cur.lastrowid == 1
        conn.commit()
        assert conn.execute('SELECT max(id) FROM t').fetchone() == (3,)
    finally:
        conn.close()
