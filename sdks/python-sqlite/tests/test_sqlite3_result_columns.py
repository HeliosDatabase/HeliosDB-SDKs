# ruff: noqa: F811  (pytest fixtures imported from the conformance module)
"""Conformance: result columns (names, duplicates, declared types, aggregate
types, ORDER BY in joins, lastrowid) against CPython's sqlite3.

Runs on the same backends as tests/test_sqlite3_conformance.py: ``embedded``
(heliosdb-nano-embedded installed) and ``daemon`` (HELIOSDB_TEST_URL set).
"""

import datetime
import sqlite3
import warnings

import pytest

import heliosdb_sqlite
from test_sqlite3_conformance import Pair, assert_same, backend, pair  # noqa: F401

pytestmark = pytest.mark.compatibility


def names(cursor):
    return [d[0] for d in cursor.description]


def same(p, sql, params=()):
    """Same rows, same value types and same cursor.description names."""
    ref, hdb = p.both(sql, params)
    expected, got = ref.fetchall(), hdb.fetchall()
    assert names(hdb) == names(ref), sql
    assert_same(expected, got)
    return got


@pytest.fixture
def family(pair):
    p, k = pair.table('p'), pair.table('k')
    pair.both(f'CREATE TABLE {p} (id INTEGER PRIMARY KEY, name TEXT)')
    pair.both(f'CREATE TABLE {k} (id INTEGER PRIMARY KEY, pid INTEGER, name TEXT, amt REAL)')
    pair.both(f"INSERT INTO {p} VALUES (1, 'parent'), (2, 'other')")
    pair.both(f"INSERT INTO {k} VALUES (10, 1, 'kid', 2.5), (11, 1, 'kid2', 1.0), "
              f"(12, 2, 'kid3', 4.0)")
    return pair, p, k


# --------------------------------------------------------------------------
# Columns that share a name
# --------------------------------------------------------------------------

def test_join_columns_with_the_same_name_are_all_returned(family):
    pair, p, k = family
    same(pair, f'SELECT {p}.id, {k}.id, {p}.name, {k}.name FROM {p} '
               f'JOIN {k} ON {k}.pid = {p}.id ORDER BY {k}.id')
    same(pair, f'SELECT * FROM {p} JOIN {k} ON {k}.pid = {p}.id ORDER BY {k}.id')
    same(pair, f'SELECT a.*, b.* FROM {p} a JOIN {k} b ON b.pid = a.id ORDER BY b.id')
    same(pair, f'SELECT a.id, b.id FROM {p} a, {k} b WHERE b.pid = a.id ORDER BY b.id')


def test_positional_access_reads_the_right_column(family):
    pair, p, k = family
    hdb = pair.hdb.execute(f'SELECT {p}.id, {k}.id, {p}.name, {k}.name FROM {p} '
                           f'JOIN {k} ON {k}.pid = {p}.id WHERE {k}.id = 10')
    row = hdb.fetchone()
    assert (row[0], row[1], row[2], row[3]) == (1, 10, 'parent', 'kid')


def test_repeated_expressions_and_literals(family):
    pair, p, k = family
    same(pair, f'SELECT count(*), count(name) FROM {k}')
    same(pair, f'SELECT sum(amt), sum(amt*2), min(amt) FROM {k}')
    same(pair, 'SELECT 1, 1, 2')
    same(pair, f'SELECT id, id FROM {p} ORDER BY 1')
    same(pair, f'SELECT upper(name), upper(name) FROM {p} ORDER BY 1')


def test_duplicate_names_in_an_empty_result(family):
    pair, p, k = family
    ref, hdb = pair.both(f'SELECT {p}.id, {k}.id FROM {p} JOIN {k} ON {k}.pid = {p}.id '
                         f'WHERE 1 = 0')
    assert hdb.fetchall() == ref.fetchall() == []
    assert names(hdb) == names(ref) == ['id', 'id']


def test_row_factory_sees_every_column(family):
    pair, p, k = family
    pair.ref.row_factory = sqlite3.Row
    pair.hdb.row_factory = heliosdb_sqlite.Row
    ref, hdb = pair.both(f'SELECT {p}.name, {k}.name AS kid FROM {p} '
                         f'JOIN {k} ON {k}.pid = {p}.id ORDER BY {k}.id')
    expected, got = ref.fetchall(), hdb.fetchall()
    assert [r.keys() for r in got] == [r.keys() for r in expected]
    assert [tuple(r) for r in got] == [tuple(r) for r in expected]


# --------------------------------------------------------------------------
# Column names (cursor.description)
# --------------------------------------------------------------------------

def test_names_follow_sqlite3(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (Id INTEGER, Name TEXT, "MiXed" TEXT, plain REAL)')
    pair.both(f"INSERT INTO {t} VALUES (1, 'x', 'm', 1.5)")
    same(pair, f'SELECT * FROM {t}')
    same(pair, f'SELECT id, NAME, "MiXed" AS "Alias", Plain FROM {t}')
    same(pair, f'SELECT count(*), sum(plain)/2, \'x\' || name, upper( name ) FROM {t} '
               f'GROUP BY name')
    same(pair, f'SELECT x.id AS Ident, x."MiXed" FROM {t} x')


def test_names_after_add_column_and_rename(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (a INTEGER)')
    pair.hdb.commit()
    pair.ref.commit()
    pair.both(f'ALTER TABLE {t} ADD COLUMN NewCol TEXT')
    pair.both(f'ALTER TABLE {t} RENAME COLUMN a TO FirstCol')
    pair.both(f"INSERT INTO {t} VALUES (1, 'n')")
    same(pair, f'SELECT * FROM {t}')


# --------------------------------------------------------------------------
# Aggregate result types
# --------------------------------------------------------------------------

def test_sum_over_real_is_a_float(family):
    pair, p, k = family
    same(pair, f'SELECT sum(amt) FROM {k}')
    same(pair, f'SELECT pid, sum(amt), avg(amt), max(amt) FROM {k} GROUP BY pid ORDER BY pid')
    same(pair, f'SELECT sum(id), sum(pid) FROM {k}')
    same(pair, f'SELECT sum(amt) * 2, sum(id) + 1 FROM {k}')


def test_numeric_looking_text_stays_text(family):
    pair, p, k = family
    same(pair, f"SELECT '3.5', '7' || '', max(name) FROM {k}")


def test_total_and_group_concat(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (g INTEGER, a REAL, s TEXT)')
    same(pair, f'SELECT total(a) FROM {t}')
    same(pair, f'SELECT avg(a), total(a), group_concat(s) FROM {t}')
    same(pair, f'SELECT group_concat(s) FROM {t} WHERE 1 = 0')
    # (one non-NULL s per group: group_concat's order is unspecified)
    pair.both(f"INSERT INTO {t} VALUES (1, 2.5, 'x'), (1, 1.0, NULL), (2, NULL, NULL)")
    same(pair, f'SELECT avg(a), total(a) FROM {t}')
    same(pair, f"SELECT g, total(a), group_concat(s, '|') FROM {t} GROUP BY g ORDER BY g")


@pytest.mark.xfail(reason='HeliosDB Nano: sum() over no rows gives 0 instead of NULL when '
                          'the query also has group_concat/string_agg (reported upstream)',
                   strict=False)
def test_sum_with_group_concat_over_no_rows(pair):
    t = pair.table()
    pair.both(f'CREATE TABLE {t} (a REAL, s TEXT)')
    same(pair, f'SELECT sum(a), group_concat(s) FROM {t}')


# --------------------------------------------------------------------------
# PARSE_DECLTYPES follows the source column, not the result name
# --------------------------------------------------------------------------

def test_decltypes_follow_the_source_column(backend):
    p = Pair(backend, detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        t = p.table()
        p.both(f'CREATE TABLE {t} (name TEXT, d DATE, n INTEGER)')
        p.both(f"INSERT INTO {t} VALUES ('x', '2026-01-02', 1)")
        assert same(p, f"SELECT 'notadate' AS d FROM {t}") == [('notadate',)]
        assert same(p, f'SELECT name AS d FROM {t}') == [('x',)]
        assert same(p, f'SELECT d AS name FROM {t}') == [(datetime.date(2026, 1, 2),)]
        same(p, f'SELECT x.d, x.n, x.n + 1 FROM {t} x')
    finally:
        p.close()


# --------------------------------------------------------------------------
# ORDER BY in joins
# --------------------------------------------------------------------------

def test_order_by_qualified_column_in_a_join(family):
    pair, p, k = family
    same(pair, f'SELECT {p}.id, {k}.id FROM {p} JOIN {k} ON {k}.pid = {p}.id '
               f'ORDER BY {k}.id DESC')
    same(pair, f'SELECT {p}.id, {k}.id FROM {p} JOIN {k} ON {k}.pid = {p}.id ORDER BY 2 DESC')
    same(pair, f'SELECT a.name, b.name FROM {p} a JOIN {k} b ON b.pid = a.id '
               f'ORDER BY a.id DESC, b.name DESC')
    same(pair, f'SELECT * FROM {p} JOIN {k} ON {k}.pid = {p}.id ORDER BY {k}.amt DESC')


# --------------------------------------------------------------------------
# lastrowid
# --------------------------------------------------------------------------

def test_lastrowid_is_the_connections_last_insert(family):
    pair, p, k = family
    ref, hdb = pair.ref.cursor(), pair.hdb.cursor()
    for cur in (ref, hdb):
        cur.execute(f"INSERT INTO {k} (pid, name, amt) VALUES (1, 'z', 0.5)")
    assert hdb.lastrowid == ref.lastrowid == 13
    for cur in (ref, hdb):
        cur.execute(f'UPDATE {k} SET amt = 1 WHERE id = 10')
    assert hdb.lastrowid == ref.lastrowid == 13
    # a new cursor reports the connection's last inserted rowid after execute()
    r2, h2 = pair.both(f'SELECT 1 FROM {k} WHERE id = 10')
    assert h2.lastrowid == r2.lastrowid == 13


def test_lastrowid_without_integer_primary_key_is_none(pair):
    """Documented difference: HeliosDB tables have no implicit rowid."""
    t = pair.table()
    pair.hdb.execute(f'CREATE TABLE {t} (v TEXT)')
    assert pair.hdb.execute(f"INSERT INTO {t} VALUES ('a')").lastrowid is None


# --------------------------------------------------------------------------
# Embedded: several databases in one process
# --------------------------------------------------------------------------

def test_integer_primary_key_with_several_databases_open(backend, tmp_path):
    if backend != 'embedded':
        pytest.skip('in-process binding only')
    a = heliosdb_sqlite.connect(str(tmp_path / 'a.db'), embedded_backend='binding')
    try:
        a.execute('CREATE TABLE k (id INTEGER PRIMARY KEY, v TEXT)')
        a.execute("INSERT INTO k VALUES (10, 'x'), (11, 'y')")
        a.commit()
        b = heliosdb_sqlite.connect(str(tmp_path / 'b.db'), embedded_backend='binding')
        try:
            b.execute('CREATE TABLE k (id INTEGER PRIMARY KEY, v TEXT)')
            assert b.execute("INSERT INTO k (v) VALUES ('b')").lastrowid == 1
            b.commit()
            assert a.execute("INSERT INTO k (v) VALUES ('z')").lastrowid == 12
            assert b.execute("INSERT INTO k (v) VALUES ('c')").lastrowid == 2
            assert a.execute("INSERT INTO k (v) VALUES ('w')").lastrowid == 13
        finally:
            b.close()
    finally:
        a.close()


# --------------------------------------------------------------------------
# VECTOR values are lists of floats in both modes
# --------------------------------------------------------------------------

def test_vector_column_is_a_list(backend):
    p = Pair(backend)
    try:
        t = p.table()
        p.hdb.execute(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, v VECTOR(2))')
        p.hdb.execute(f'INSERT INTO {t} (v) VALUES (?)', ([1.0, 2.5],))
        assert p.hdb.execute(f'SELECT v FROM {t}').fetchall() == [([1.0, 2.5],)]
    finally:
        p.close()


def test_no_warning_for_ordinary_queries(family):
    pair, p, k = family
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        pair.hdb.execute(f'SELECT * FROM {p} JOIN {k} ON {k}.pid = {p}.id').fetchall()
        pair.hdb.execute(f'SELECT count(*), sum(amt), total(amt) FROM {k}').fetchall()


def test_catalog_from_an_older_version_gains_names(backend, tmp_path):
    if backend != 'embedded':
        pytest.skip('needs a fresh database')
    path = str(tmp_path / 'old.db')
    conn = heliosdb_sqlite.connect(path, embedded_backend='binding')
    try:
        # the catalog as versions before 3.1 created it
        conn.execute('CREATE TABLE heliosdb_sqlite_decltypes (table_name TEXT NOT NULL, '
                     'column_name TEXT NOT NULL, decltype TEXT NOT NULL)')
        conn.execute("INSERT INTO heliosdb_sqlite_decltypes VALUES ('old', 'd', 'DATE')")
        conn.execute('CREATE TABLE old (d DATE)')
        conn.commit()
    finally:
        conn.close()
    conn = heliosdb_sqlite.connect(path, embedded_backend='binding',
                                   detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        conn.execute("INSERT INTO old VALUES ('2026-01-02')")
        # reading does not alter the catalog, and declared types still work
        assert conn.execute('SELECT d FROM old').fetchall() == [(datetime.date(2026, 1, 2),)]
        conn.execute('CREATE TABLE newer (Id INTEGER, Label TEXT)')
        assert names(conn.execute('SELECT * FROM newer')) == ['Id', 'Label']
        assert conn.execute('SELECT d FROM old').fetchall() == [(datetime.date(2026, 1, 2),)]
    finally:
        conn.close()
