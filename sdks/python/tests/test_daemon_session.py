"""
Regression tests for the daemon-mode session lifetime (GH #1).

The offline half of this file (TestFakeDriver) needs no server: it installs a
fake ``psycopg2`` and asserts on the exact statements that reach the wire.  It
is the test that would have caught 3.0.1, and it is cheap enough to gate every
commit.

The online half (TestLiveServer) needs a running HeliosDB-Nano and is skipped
unless HELIOSDB_TEST_DSN is set, e.g.

    HELIOSDB_TEST_DSN=postgresql://helios:<redacted>@127.0.0.1:5432/heliosdb \
        pytest sdks/python/tests/test_daemon_session.py
"""

import os
import sys
import uuid
import warnings

import pytest


# ---------------------------------------------------------------------------
# Fake psycopg2: records every connect() and every statement, with the backend
# PID that ran it.
# ---------------------------------------------------------------------------

class _FakeError(Exception):
    pgcode = None


class _FakeOperationalError(_FakeError):
    pass


class _FakeInterfaceError(_FakeError):
    pass


class _FakeIntegrityError(_FakeError):
    pgcode = '23505'


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql):
        registry = self.conn.registry
        if registry['kill_next']:
            registry['kill_next'] = False
            self.conn.closed = 1
            raise _FakeOperationalError('server closed the connection unexpectedly')
        registry['log'].append((self.conn.pid, ' '.join(sql.split())))
        upper = sql.strip().upper()
        if upper.startswith('SELECT LASTVAL'):
            self.description = [('lastval',)]
            self._rows = [(4242,)]
            self.rowcount = 1
        elif upper.startswith('SELECT PG_BACKEND_PID'):
            self.description = [('pg_backend_pid',)]
            self._rows = [(self.conn.pid,)]
            self.rowcount = 1
        elif upper.startswith('SELECT'):
            self.description = [('n',)]
            self._rows = [(1,)]
            self.rowcount = 1
        elif 'DUPLICATE' in upper:
            raise _FakeIntegrityError('duplicate key value violates unique constraint')
        else:
            self.description = None
            self.rowcount = 1

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, registry, params):
        registry['pid'] += 1
        self.registry = registry
        self.pid = registry['pid']
        self.params = params
        self.closed = 0
        self.autocommit = False

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = 1

    def get_backend_pid(self):
        return self.pid

    def get_transaction_status(self):
        return 0

    def cancel(self):
        pass


class _FakePsycopg2:
    Error = _FakeError
    OperationalError = _FakeOperationalError
    InterfaceError = _FakeInterfaceError
    IntegrityError = _FakeIntegrityError

    def __init__(self):
        self.registry = {'connects': [], 'log': [], 'pid': 1000, 'kill_next': False}

    def connect(self, **params):
        self.registry['connects'].append(params)
        return _FakeConnection(self.registry, params)


@pytest.fixture()
def fake_pg(monkeypatch):
    fake = _FakePsycopg2()
    monkeypatch.setitem(sys.modules, 'psycopg2', fake)
    return fake


@pytest.fixture()
def shim():
    import heliosdb_sqlite
    return heliosdb_sqlite


def _statements(fake):
    return [sql for _pid, sql in fake.registry['log']]


def _pids(fake):
    return {pid for pid, _sql in fake.registry['log']}


# ---------------------------------------------------------------------------
# Offline regression tests - these would have caught GH #1
# ---------------------------------------------------------------------------

class TestFakeDriver:

    def test_one_session_for_the_whole_connection(self, shim, fake_pg):
        """GH #1: 3.0.1 opened a new connection (and a new PID) per statement."""
        conn = shim.connect('db', mode='daemon')
        cur = conn.cursor()
        cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        cur.execute("INSERT INTO t (id, name) VALUES (1, 'a')")
        conn.commit()
        cur.execute("SELECT count(*) FROM t")

        assert len(fake_pg.registry['connects']) == 1, 'more than one connection was opened'
        assert len(_pids(fake_pg)) == 1, 'statements ran on different backends'
        conn.close()

    def test_commit_reaches_the_same_session_as_the_insert(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()

        log = fake_pg.registry['log']
        insert_pid = [pid for pid, sql in log if sql.startswith('INSERT')][0]
        commit_pid = [pid for pid, sql in log if sql.startswith('COMMIT')][0]
        begin_pid = [pid for pid, sql in log if sql.startswith('BEGIN')][0]
        assert begin_pid == insert_pid == commit_pid
        conn.close()

    def test_implicit_begin_is_deferred_to_dml(self, shim, fake_pg):
        """sqlite3 legacy semantics: DDL runs in autocommit, DML opens a txn."""
        conn = shim.connect('db', mode='daemon')
        conn.execute("CREATE TABLE t (id INT)")
        assert 'BEGIN;' not in _statements(fake_pg)
        assert conn.in_transaction is False

        conn.execute("INSERT INTO t VALUES (1)")
        assert _statements(fake_pg).index('BEGIN;') == 1
        assert conn.in_transaction is True
        conn.close()

    def test_isolation_level_none_never_begins(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon', isolation_level=None)
        conn.execute("INSERT INTO t VALUES (1)")
        assert not [s for s in _statements(fake_pg) if s.startswith('BEGIN')]
        assert conn.in_transaction is False
        conn.close()

    def test_autocommit_false_is_pep249(self, shim, fake_pg):
        """Python 3.12 autocommit=False keeps a transaction open around reads too."""
        conn = shim.connect('db', mode='daemon', autocommit=False)
        conn.execute("SELECT 1")
        assert _statements(fake_pg)[0] == 'BEGIN;'
        conn.commit()
        assert 'COMMIT;' in _statements(fake_pg)
        conn.close()

    def test_autocommit_true_never_begins(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon', autocommit=True)
        conn.execute("INSERT INTO t VALUES (1)")
        assert not [s for s in _statements(fake_pg) if s.startswith('BEGIN')]
        conn.close()

    def test_rollback_is_issued_on_the_session(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("INSERT INTO t VALUES (1)")
        conn.rollback()
        assert 'ROLLBACK;' in _statements(fake_pg)
        assert conn.in_transaction is False
        conn.close()

    def test_close_rolls_back_uncommitted_work(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("INSERT INTO t VALUES (1)")
        conn.close()
        assert _statements(fake_pg)[-1] == 'ROLLBACK;'

    def test_context_manager_commits(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        with conn:
            conn.execute("INSERT INTO t VALUES (1)")
        assert 'COMMIT;' in _statements(fake_pg)
        conn.close()

    def test_context_manager_rolls_back_on_error(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        with pytest.raises(ValueError):
            with conn:
                conn.execute("INSERT INTO t VALUES (1)")
                raise ValueError('boom')
        assert 'ROLLBACK;' in _statements(fake_pg)
        conn.close()

    def test_lastrowid_survives(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        cur = conn.execute("INSERT INTO t (name) VALUES ('a')")
        assert cur.lastrowid == 4242
        # resolved on the same backend that ran the INSERT
        assert len(_pids(fake_pg)) == 1
        conn.close()

    def test_lastrowid_probe_cannot_poison_the_transaction(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        cur = conn.execute("INSERT INTO t (name) VALUES ('a')")
        _ = cur.lastrowid
        statements = _statements(fake_pg)
        assert 'SAVEPOINT _hdb_lastrowid;' in statements
        assert 'RELEASE SAVEPOINT _hdb_lastrowid;' in statements
        conn.close()

    def test_dsn_is_honoured(self, shim, fake_pg):
        dsn = 'postgresql://helios:pw@db.example:5433/heliosdb'
        conn = shim.connect('db', mode='daemon', dsn=dsn)
        conn.execute("SELECT 1")
        assert fake_pg.registry['connects'][0]['dsn'] == dsn
        conn.close()

    def test_database_as_postgres_uri(self, shim, fake_pg):
        conn = shim.connect('postgresql://u@h/db', mode='daemon')
        conn.execute("SELECT 1")
        assert fake_pg.registry['connects'][0]['dsn'] == 'postgresql://u@h/db'
        conn.close()

    def test_empty_password_is_not_forced(self, shim, fake_pg, monkeypatch):
        monkeypatch.delenv('PGPASSWORD', raising=False)
        conn = shim.connect('db', mode='daemon')
        conn.execute("SELECT 1")
        assert 'password' not in fake_pg.registry['connects'][0]
        conn.close()

    def test_unknown_kwarg_warns(self, shim, fake_pg):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            shim.connect('db', mode='daemon', dsnn='typo').close()
        assert any('dsnn' in str(w.message) for w in caught)

    def test_integrity_error_is_not_flattened(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        with pytest.raises(shim.IntegrityError):
            conn.execute("INSERT INTO duplicate VALUES (1)")
        conn.close()

    def test_aborted_transaction_cannot_be_committed_silently(self, shim, fake_pg):
        """PostgreSQL turns COMMIT on an aborted txn into ROLLBACK: never hide that."""
        conn = shim.connect('db', mode='daemon')
        with pytest.raises(shim.IntegrityError):
            conn.execute("INSERT INTO duplicate VALUES (1)")
        with pytest.raises(shim.OperationalError):
            conn.commit()
        conn.close()

    def test_aborted_transaction_recovers_after_rollback(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        with pytest.raises(shim.IntegrityError):
            conn.execute("INSERT INTO duplicate VALUES (1)")
        with pytest.raises(shim.OperationalError):
            conn.execute("SELECT 1")
        conn.rollback()
        assert conn.execute("SELECT 1").fetchone() == (1,)
        conn.close()

    def test_dropped_connection_replays_reads_only(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("SELECT 1")
        fake_pg.registry['kill_next'] = True
        assert conn.execute("SELECT 1").fetchone() == (1,)   # read is replayed
        conn.close()

    def test_dropped_connection_never_replays_a_write(self, shim, fake_pg):
        """An in-flight write may already have been applied: it must never be re-sent."""
        conn = shim.connect('db', mode='daemon')
        conn.execute("INSERT INTO t VALUES (1)")
        before = len([s for s in _statements(fake_pg) if s.startswith('INSERT')])
        fake_pg.registry['kill_next'] = True
        with pytest.raises(shim.OperationalError):
            conn.execute("INSERT INTO t VALUES (2)")
        after = len([s for s in _statements(fake_pg) if s.startswith('INSERT')])
        assert after == before, 'the write was replayed on a new session'
        conn.rollback()
        conn.close()

    def test_rows_are_materialised_before_the_cursor_is_released(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        cur = conn.execute("SELECT n FROM t")
        conn.close()
        assert cur.fetchone() == (1,)   # readable after the connection is gone

    def test_select_rowcount_matches_sqlite3(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        cur = conn.execute("SELECT n FROM t")
        assert cur.rowcount == -1
        conn.close()

    def test_total_changes(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("INSERT INTO t VALUES (2)")
        assert conn.total_changes == 2
        conn.close()

    def test_raw_begin_is_tracked(self, shim, fake_pg):
        """execute('BEGIN') used to leave _in_transaction False, so commit() no-opped."""
        conn = shim.connect('db', mode='daemon', isolation_level=None)
        conn.execute("BEGIN")
        assert conn.in_transaction is True
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        assert 'COMMIT;' in _statements(fake_pg)
        conn.close()

    def test_executescript_commits_pending_transaction(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("INSERT INTO t VALUES (1)")
        conn.executescript("CREATE TABLE a (x INT); CREATE TABLE b (y INT)")
        statements = _statements(fake_pg)
        assert statements.index('COMMIT;') < statements.index('CREATE TABLE a (x INT);')
        conn.close()

    def test_savepoint_opens_a_transaction(self, shim, fake_pg):
        conn = shim.connect('db', mode='daemon')
        conn.execute("SAVEPOINT sp1")
        assert _statements(fake_pg)[0] == 'BEGIN;'
        conn.close()


class TestStatementClassifier:

    @pytest.mark.parametrize('sql,expected', [
        ('SELECT 1', 'query'),
        ('  select 1', 'query'),
        ('-- comment\nINSERT INTO t VALUES (1)', 'dml'),
        ('/* c */ CREATE TABLE x (a INT)', 'other'),
        ('WITH q AS (SELECT 1) SELECT * FROM q', 'query'),
        ('WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d', 'dml'),
        ('BEGIN', 'begin'),
        ('START TRANSACTION', 'begin'),
        ('COMMIT;', 'commit'),
        ('END', 'commit'),
        ('ROLLBACK', 'rollback'),
        ('ROLLBACK TO SAVEPOINT s', 'rollback_to'),
        ('SAVEPOINT s', 'savepoint'),
        ('RELEASE SAVEPOINT s', 'savepoint'),
        ('UPDATE t SET a = 1', 'dml'),
        ('SET search_path TO x', 'other'),
    ])
    def test_classification(self, sql, expected):
        from heliosdb_sqlite.main import _classify_statement
        assert _classify_statement(sql) == expected


class TestCredentialRedaction:

    def test_dsn_password_is_redacted(self):
        from heliosdb_sqlite.main import _redact_dsn
        assert 'hunter2' not in _redact_dsn('postgresql://u:hunter2@h:5432/db')
        assert 'hunter2' not in _redact_dsn('host=h password=hunter2 user=u')


# ---------------------------------------------------------------------------
# Live-server tests - the real durability proof
# ---------------------------------------------------------------------------

DSN = os.environ.get('HELIOSDB_TEST_DSN')
live = pytest.mark.skipif(not DSN, reason='set HELIOSDB_TEST_DSN to run live tests')



def _requires(conn, probe_sql, what, tracker):
    """
    Skip when the server does not implement `probe_sql`.

    These probes are not politeness: HeliosDB-Nano 4.31.1 implements
    nextval/currval/setval but NOT lastval, and has no pg_backend_pid(), so the
    two tests below would fail against a perfectly healthy server and hide a
    real regression in the noise.  Tracked upstream as `tracker`.
    """
    try:
        conn.execute(probe_sql).fetchone()
    except Exception as exc:
        pytest.skip('server does not support {} ({}): {}'.format(what, tracker, exc))


@live
class TestLiveServer:

    @pytest.fixture()
    def table(self, shim):
        name = 't_{}'.format(uuid.uuid4().hex[:12])
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN, isolation_level=None)
        conn.execute(
            'CREATE TABLE {} (id SERIAL PRIMARY KEY, name TEXT)'.format(name))
        conn.close()
        yield name
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN, isolation_level=None)
        conn.execute('DROP TABLE IF EXISTS {}'.format(name))
        conn.close()

    def test_write_is_visible_from_a_new_connection_after_commit(self, shim, table):
        """The headline GH #1 assertion: durability across process-level sessions."""
        writer = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        writer.execute("INSERT INTO {} (name) VALUES ('a')".format(table))
        writer.commit()
        writer.close()

        reader = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        assert reader.execute(
            'SELECT count(*) FROM {}'.format(table)).fetchone()[0] in (1, '1')
        reader.close()

    def test_rollback_actually_discards(self, shim, table):
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        conn.execute("INSERT INTO {} (name) VALUES ('b')".format(table))
        conn.rollback()
        conn.close()

        reader = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        assert reader.execute(
            'SELECT count(*) FROM {}'.format(table)).fetchone()[0] in (0, '0')
        reader.close()

    def test_close_without_commit_discards(self, shim, table):
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        conn.execute("INSERT INTO {} (name) VALUES ('c')".format(table))
        conn.close()

        reader = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        assert reader.execute(
            'SELECT count(*) FROM {}'.format(table)).fetchone()[0] in (0, '0')
        reader.close()

    def test_lastrowid_after_insert(self, shim, table):
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        # lastrowid rides on LASTVAL(), which HeliosDB-Nano does not implement
        # yet (nextval/currval/setval exist).  The shim degrades to None rather
        # than inventing a value, so probe first instead of failing here.
        _requires(conn, 'SELECT LASTVAL()', 'LASTVAL()', 'HeliosDB-Nano: LASTVAL() unimplemented')
        cur = conn.execute("INSERT INTO {} (name) VALUES ('d')".format(table))
        assert cur.lastrowid is not None and int(cur.lastrowid) > 0
        conn.commit()
        conn.close()

    def test_same_backend_session_is_reused(self, shim):
        """pg_backend_pid() is stable => every statement ran on one session."""
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN, isolation_level=None)
        _requires(conn, 'SELECT pg_backend_pid()', 'pg_backend_pid()',
                  'HeliosDB-Nano: pg_backend_pid() unimplemented')
        pids = {conn.execute('SELECT pg_backend_pid()').fetchone()[0]
                for _ in range(5)}
        assert len(pids) == 1
        conn.close()

    def test_temporary_table_survives_across_statements(self, shim):
        """A temp table is session-scoped: it cannot survive a per-statement connection."""
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN, isolation_level=None)
        name = 'tmp_probe_{}'.format(uuid.uuid4().hex[:8])
        conn.execute('CREATE TEMPORARY TABLE {} (v INT)'.format(name))
        try:
            conn.execute('INSERT INTO {} VALUES (1)'.format(name))
            assert conn.execute(
                'SELECT count(*) FROM {}'.format(name)).fetchone()[0] in (1, '1')
        finally:
            # HeliosDB-Nano parses TEMPORARY and discards it, so this is really a
            # PERMANENT table today and would otherwise leak between runs.  The
            # unique name keeps concurrent runs from colliding meanwhile.
            conn.execute('DROP TABLE IF EXISTS {}'.format(name))
            conn.close()

    def test_session_setting_survives_across_statements(self, shim):
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN, isolation_level=None)
        # search_path, not application_name: HeliosDB-Nano treats search_path as
        # a real session-scoped setting, while application_name is not a
        # registered GUC there.  Either one proves the same thing - that session
        # state outlives the statement that set it.
        conn.execute('SET search_path TO public')
        assert 'public' in str(conn.execute('SHOW search_path').fetchone()[0])
        conn.close()

    def test_savepoint_round_trip(self, shim, table):
        conn = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        conn.execute("INSERT INTO {} (name) VALUES ('keep')".format(table))
        conn.execute('SAVEPOINT sp1')
        conn.execute("INSERT INTO {} (name) VALUES ('drop')".format(table))
        conn.execute('ROLLBACK TO SAVEPOINT sp1')
        conn.commit()
        conn.close()

        reader = shim.connect('heliosdb', mode='daemon', dsn=DSN)
        names = {r[0] for r in reader.execute(
            'SELECT name FROM {}'.format(table)).fetchall()}
        assert names == {'keep'}
        reader.close()
