"""Unit tests for heliosdb_sqlite cursor.lastrowid support.

The engine is replaced by a tiny in-process fake so the tests run without a
HeliosDB binary: the fake answers ``PRAGMA table_info`` from a declared
schema and echoes generated primary keys for ``INSERT ... RETURNING``.
"""

import re

import pytest

import heliosdb_sqlite
from heliosdb_sqlite.main import Connection


class FakeEngine:
    def __init__(self, schemas):
        # schemas: {table: [(name, type, is_pk), ...]}
        self.schemas = schemas
        self.next_id = {t: 1 for t in schemas}
        self.statements = []

    def __call__(self, sql):
        self.statements.append(sql)
        up = sql.strip().upper()
        m = re.match(r'PRAGMA TABLE_INFO\((\w+)\)', up)
        if m:
            table = m.group(1).lower()
            if table not in self.schemas:
                raise RuntimeError(f"no such table: {table}")
            rows = [
                [str(i), name, typ, '0', None, '1' if pk else '0']
                for i, (name, typ, pk) in enumerate(self.schemas[table])
            ]
            return {'rows': rows, 'columns': ['cid', 'name', 'type', 'notnull', 'dflt_value', 'pk']}
        m = re.match(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(?:\w+\.)?"?(\w+)"?', sql.strip(), re.I)
        if m:
            table = m.group(1).lower()
            nrows = max(1, sql.upper().count('),') + 1) if 'VALUES' in up else 1
            ids = []
            for _ in range(nrows):
                ids.append(self.next_id[table])
                self.next_id[table] += 1
            r = re.search(r'RETURNING\s+"?(\w+)"?', sql, re.I)
            if r:
                return {'rows': [[str(i)] for i in ids], 'columns': [r.group(1)]}
            return nrows
        if up.startswith('SELECT'):
            return {'rows': [['1']], 'columns': ['x']}
        return 0


def make_conn(schemas, **kwargs):
    engine = FakeEngine(schemas)

    class _Conn(Connection):
        def _initialize_heliosdb(self):
            self._heliosdb_process = None

        def _execute_sql(self, sql):
            return engine(sql)

    conn = _Conn(':memory:', isolation_level=None, **kwargs)
    return conn, engine


SCHEMAS = {
    'users': [('id', 'INTEGER', True), ('name', 'TEXT', False)],
    'tags': [('slug', 'TEXT', True), ('label', 'TEXT', False)],
    'events': [('id', 'BIGSERIAL', True), ('kind', 'TEXT', False)],
}


def test_lastrowid_set_for_integer_pk():
    conn, engine = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a')")
    assert cur.lastrowid == 1
    cur.execute("INSERT INTO users (name) VALUES ('b');")
    assert cur.lastrowid == 2
    # RETURNING was injected, trailing ';' stripped first
    assert engine.statements[-1].endswith('RETURNING "id"')
    # The synthesised result set is hidden from the caller
    assert cur.description is None
    assert cur.fetchall() == []
    assert cur.rowcount == 1


def test_serial_pk_detected():
    conn, _ = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("INSERT INTO events (kind) VALUES ('x')")
    assert cur.lastrowid == 1


def test_multirow_insert_reports_last_id_and_rowcount():
    conn, _ = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a'), ('b'), ('c')")
    assert cur.lastrowid == 3
    assert cur.rowcount == 3


def test_text_pk_leaves_lastrowid_none_and_no_rewrite():
    conn, engine = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a')")
    assert cur.lastrowid == 1
    cur.execute("INSERT INTO tags (slug, label) VALUES ('s', 'l')")
    assert cur.lastrowid is None  # cleared on every INSERT, as in sqlite3
    assert 'RETURNING' not in engine.statements[-1].upper()


def test_non_insert_keeps_previous_lastrowid():
    conn, _ = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a')")
    cur.execute("SELECT 1")
    assert cur.lastrowid == 1
    assert cur.fetchall()[0][0] == '1'


def test_user_supplied_returning_not_rewritten():
    conn, engine = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a') RETURNING id, name")
    assert engine.statements[-1].count('RETURNING') == 1
    # caller asked for RETURNING, so they get the rows back
    assert [tuple(r) for r in cur.fetchall()] == [('1',)]


def test_schema_prefix_and_quoted_names():
    conn, engine = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute('INSERT INTO public.users (name) VALUES (\'a\')')
    assert cur.lastrowid == 1
    cur.execute('INSERT INTO "users" (name) VALUES (\'b\')')
    assert cur.lastrowid == 2
    pragmas = [s for s in engine.statements if s.upper().startswith('PRAGMA')]
    assert pragmas == ['PRAGMA table_info(users)']  # cached across statements


def test_pk_cache_shared_across_cursors():
    conn, engine = make_conn(SCHEMAS)
    conn.cursor().execute("INSERT INTO users (name) VALUES ('a')")
    conn.cursor().execute("INSERT INTO users (name) VALUES ('b')")
    assert sum(1 for s in engine.statements if s.upper().startswith('PRAGMA')) == 1


def test_catalog_lookup_failure_falls_back_without_rewrite():
    conn, engine = make_conn({'users': SCHEMAS['users']})
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a')")
    assert cur.lastrowid == 1
    # PRAGMA for an unknown table raises inside the fake engine
    engine.next_id['other'] = 1
    cur.execute("INSERT INTO other (x) VALUES (1)")
    assert cur.lastrowid is None
    assert 'RETURNING' not in engine.statements[-1].upper()
    assert conn._lastrowid_pk_cache['other'] is None


@pytest.mark.parametrize('kwargs', [{'lastrowid': False}, {'lastrowid_disabled': True}])
def test_opt_out(kwargs):
    conn, engine = make_conn(SCHEMAS, **kwargs)
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name) VALUES ('a')")
    assert cur.lastrowid is None
    assert not any(s.upper().startswith('PRAGMA') for s in engine.statements)
    assert 'RETURNING' not in engine.statements[-1].upper()


def test_with_cte_insert_is_not_rewritten():
    conn, engine = make_conn(SCHEMAS)
    cur = conn.cursor()
    cur.execute("WITH x AS (SELECT 1) INSERT INTO users (name) SELECT 'a' FROM x")
    assert 'RETURNING' not in engine.statements[-1].upper()


BOX_OUTPUT = """\
┌─────┬──────┐
│ cid │ name │
├─────┼──────┤
│ 0   │ id   │
├─────┼──────┤
│ 1   │ name │
└─────┴──────┘
(2 rows)
"""


def test_repl_box_table_parser_with_row_dividers():
    conn, _ = make_conn(SCHEMAS)
    res = Connection._parse_repl_output(conn, BOX_OUTPUT, 'PRAGMA table_info(users)')
    assert res['columns'] == ['cid', 'name']
    assert res['rows'] == [['0', 'id'], ['1', 'name']]


def test_repl_parser_treats_returning_as_query():
    conn, _ = make_conn(SCHEMAS)
    out = "┌────┐\n│ id │\n├────┤\n│ 7  │\n└────┘\n"
    res = Connection._parse_repl_output(conn, out, 'INSERT INTO users (name) VALUES (1) RETURNING "id"')
    assert res == {'rows': [['7']], 'columns': ['id']}


def test_module_exports_connect():
    assert callable(heliosdb_sqlite.connect)
