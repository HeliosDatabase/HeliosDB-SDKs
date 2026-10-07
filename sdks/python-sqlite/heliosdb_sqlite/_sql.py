"""Small, dependency-free SQL text helpers for heliosdb_sqlite.

Everything here works on the *code* parts of a statement only: text inside
single-quoted strings, double-quoted identifiers, ``--`` line comments and
``/* */`` block comments is never matched. That is what keeps a ``?`` inside
``'is it?'`` from being treated as a parameter, and a ``;`` inside a string
from splitting a script.

Used for:

* parameter placeholders (``?``, ``?NNN``, ``:name``, ``@name``, ``$name``
  as in sqlite3, plus HeliosDB's native ``$1``),
* the leading keyword of a statement and keyword searches (``RETURNING``),
* splitting a script into statements (``executescript``),
* mapping SQLite column types in DDL to the HeliosDB types with the same
  meaning (64-bit integers, 8-byte REAL, BLOB, DATETIME, rowid-style
  ``INTEGER PRIMARY KEY``).
"""

import re
from typing import Iterator, List, NamedTuple, Optional, Tuple

__all__ = [
    'Placeholder',
    'code_spans',
    'find_placeholders',
    'first_keyword',
    'has_keyword',
    'split_statements',
    'rewrite_ddl_types',
    'referenced_tables',
    'parse_create_table',
    'parse_insert',
    'dropped_tables',
    'parse_alter_table',
    'rowid_sequence_name',
    'identifier_name',
]


def code_spans(sql: str, keep_identifiers: bool = False) -> Iterator[Tuple[int, int]]:
    """Yield ``(start, end)`` ranges of ``sql`` that are SQL code, i.e. not
    inside a string literal, quoted identifier or comment. With
    ``keep_identifiers``, quoted identifiers count as code."""
    i, n = 0, len(sql)
    start = 0
    while i < n:
        ch = sql[i]
        if keep_identifiers and (ch == '"' or ch == '`'):
            end = sql.find(ch, i + 1)
            i = n if end < 0 else end + 1
        elif ch == "'" or ch == '"' or ch == '`':
            if start < i:
                yield start, i
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # doubled = escaped
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            start = i
        elif ch == '-' and sql.startswith('--', i):
            if start < i:
                yield start, i
            nl = sql.find('\n', i)
            i = n if nl < 0 else nl + 1
            start = i
        elif ch == '/' and sql.startswith('/*', i):
            if start < i:
                yield start, i
            end = sql.find('*/', i + 2)
            i = n if end < 0 else end + 2
            start = i
        else:
            i += 1
    if start < n:
        yield start, n


class Placeholder(NamedTuple):
    start: int
    end: int
    kind: str           # 'qmark' | 'numbered' | 'named' | 'native'
    key: Optional[object]  # None | int (?NNN / $N) | str (name)


# '::' is PostgreSQL's cast operator and must not start a ':name' parameter.
_PLACEHOLDER_RE = re.compile(
    r'(?P<cast>::)'
    r'|\?(?P<num>\d+)?'
    r'|\$(?P<native>\d+)'
    r'|[:@$](?P<name>[A-Za-z_][A-Za-z0-9_]*)'
)


def find_placeholders(sql: str) -> List[Placeholder]:
    """All parameter placeholders in ``sql``, in order of appearance."""
    if not any(c in sql for c in '?:@$'):
        return []
    found: List[Placeholder] = []
    for start, end in code_spans(sql):
        for m in _PLACEHOLDER_RE.finditer(sql, start, end):
            if m.group('cast'):
                continue
            if m.group('native') is not None:
                found.append(Placeholder(m.start(), m.end(), 'native', int(m.group('native'))))
            elif m.group('name') is not None:
                found.append(Placeholder(m.start(), m.end(), 'named', m.group('name')))
            elif m.group('num') is not None:
                found.append(Placeholder(m.start(), m.end(), 'numbered', int(m.group('num'))))
            else:
                found.append(Placeholder(m.start(), m.end(), 'qmark', None))
    return found


_WORD_RE = re.compile(r'[A-Za-z_]+')


def first_keyword(sql: str) -> str:
    """The statement's leading keyword, upper-cased ('' if none). Leading
    comments, whitespace and opening parentheses are skipped."""
    for start, end in code_spans(sql):
        m = _WORD_RE.search(sql, start, end)
        if m:
            return m.group(0).upper()
        if sql[start:end].strip(' \t\r\n(;'):
            return ''
    return ''


def has_keyword(sql: str, keyword: str) -> bool:
    """True when ``keyword`` appears as a whole word in the code of ``sql``."""
    if keyword.lower() not in sql.lower():
        return False
    pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
    return any(pattern.search(sql, s, e) for s, e in code_spans(sql))


def split_statements(script: str) -> List[str]:
    """Split a script on ``;`` outside strings and comments. Empty statements
    (and comment-only ones) are dropped; each statement keeps its text
    without the terminating ``;``."""
    cuts = []
    for start, end in code_spans(script):
        pos = script.find(';', start, end)
        while pos >= 0:
            cuts.append(pos)
            pos = script.find(';', pos + 1, end)
    statements = []
    prev = 0
    for cut in cuts + [len(script)]:
        stmt = script[prev:cut]
        prev = cut + 1
        if any(stmt[s:e].strip() for s, e in code_spans(stmt)):
            statements.append(stmt.strip())
    return statements


# --------------------------------------------------------------------------
# SQLite column types -> HeliosDB column types (CREATE TABLE / ALTER TABLE)
# --------------------------------------------------------------------------
#
# SQLite stores every integer in 64 bits and every REAL as an 8-byte double,
# whatever the declared type name says, accepts any type name (its affinity
# rules decide how values are stored), and makes ``INTEGER PRIMARY KEY`` an
# alias for the rowid, assigned automatically as one more than the largest
# key when an INSERT omits it or gives NULL. HeliosDB follows PostgreSQL
# (INTEGER is 32-bit, REAL is 4-byte) and rejects type names it does not
# know. So that schemas written for SQLite keep their meaning, column types
# are mapped as follows:
#
#   INT, INTEGER, TINYINT, SMALLINT, MEDIUMINT, BIGINT, INT2, INT8,
#   UNSIGNED BIG INT                       -> BIGINT
#   INTEGER PRIMARY KEY / INT PRIMARY KEY  -> BIGINT PRIMARY KEY
#   (also PRIMARY KEY (col) on such a col)    DEFAULT nextval('<t>_<c>_rowid_seq')
#                                             (rowid alias; see rowid_sequence_name)
#   REAL, FLOAT, DOUBLE                    -> DOUBLE PRECISION
#   BLOB                                   -> BYTEA
#   DATETIME                               -> TIMESTAMP
#   BOOL                                   -> BOOLEAN
#
# Type names HeliosDB knows (TEXT, VARCHAR(n), NUMERIC(p,s), BOOLEAN, DATE,
# TIMESTAMP, UUID, JSON, VECTOR(n), arrays, ...) are left as written. Any
# other name (POINT, MYTYPE, MONEY, NVARCHAR, ...) is mapped by SQLite's
# affinity rules: a name containing INT -> BIGINT; CHAR, CLOB or TEXT ->
# TEXT; BLOB -> BYTEA; REAL, FLOA or DOUB -> DOUBLE PRECISION; anything else
# -> TEXT (custom types are normally stored through an adapter that returns
# text). The declared name itself is kept by the caller for PARSE_DECLTYPES.

_INTEGER_TYPES = frozenset((
    'INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT', 'INT2', 'INT8',
    'UNSIGNED BIG INT',
))
_TYPE_MAP = {
    'REAL': 'DOUBLE PRECISION',
    'FLOAT': 'DOUBLE PRECISION',
    'DOUBLE': 'DOUBLE PRECISION',
    'DOUBLE PRECISION': 'DOUBLE PRECISION',
    'BLOB': 'BYTEA',
    'DATETIME': 'TIMESTAMP',
    'BOOL': 'BOOLEAN',
}
# Type names HeliosDB Nano accepts as written (first word, upper-cased).
_ENGINE_TYPES = frozenset((
    'TEXT', 'VARCHAR', 'CHAR', 'CHARACTER', 'CLOB', 'NVARCHAR', 'NAME',
    'NUMERIC', 'DECIMAL', 'BOOLEAN', 'DATE', 'TIME', 'TIMESTAMP', 'TIMESTAMPTZ',
    'INTERVAL', 'UUID', 'JSON', 'JSONB', 'VECTOR', 'BYTEA', 'POINT', 'BIT',
    'TSVECTOR', 'SERIAL', 'BIGSERIAL', 'SMALLSERIAL', 'INT4', 'FLOAT4', 'FLOAT8',
))
_TABLE_CONSTRAINT_WORDS = frozenset((
    'CONSTRAINT', 'PRIMARY', 'UNIQUE', 'CHECK', 'FOREIGN', 'EXCLUDE', 'LIKE',
))
_COLUMN_CONSTRAINT_WORDS = frozenset((
    'PRIMARY', 'NOT', 'NULL', 'DEFAULT', 'UNIQUE', 'CHECK', 'REFERENCES', 'COLLATE',
    'GENERATED', 'CONSTRAINT', 'AUTOINCREMENT', 'AS', 'ON',
))
_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$]+)'
_CREATE_TABLE_RE = re.compile(
    r'\s*CREATE\s+(?:(?:GLOBAL|LOCAL|TEMP|TEMPORARY|UNLOGGED)\s+)*TABLE\s+'
    r'(?P<ine>IF\s+NOT\s+EXISTS\s+)?(?P<n1>' + _IDENT + r')(?:\s*\.\s*(?P<n2>' + _IDENT + r'))?\s*\(',
    re.IGNORECASE,
)
_ALTER_ADD_RE = re.compile(
    r'\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?'
    r'\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?',
    re.IGNORECASE,
)
_NAME_RE = re.compile(r'\s*("[^"]*"|`[^`]*`|\[[^\]]*\]|[\w$]+)')
_TYPE_WORD_RE = re.compile(r'\s*([A-Za-z_]\w*)')
_PRIMARY_KEY_RE = re.compile(r'\bPRIMARY\s+KEY\b', re.IGNORECASE)
_AUTOINCREMENT_RE = re.compile(r'\s*\bAUTOINCREMENT\b', re.IGNORECASE)
_DEFAULT_RE = re.compile(r'\bDEFAULT\b', re.IGNORECASE)
_TABLE_PK_RE = re.compile(
    r'\s*(?:CONSTRAINT\s+' + _IDENT + r'\s+)?PRIMARY\s+KEY\s*\(\s*(' + _IDENT + r')'
    r'(?:\s+(?:ASC|DESC))?\s*\)', re.IGNORECASE,
)

ROWID_SEQUENCE_SUFFIX = '_rowid_seq'


def identifier_name(ident: str) -> str:
    """The name the engine stores for an identifier: quoted names as
    written, unquoted names lower-cased."""
    ident = ident.strip()
    if len(ident) >= 2 and ident[0] in '"`[' and ident[-1] in '"`]':
        return ident[1:-1]
    return ident.lower()


def rowid_sequence_name(table: str, column: str) -> str:
    """Name of the sequence that assigns ``table.column`` (an INTEGER
    PRIMARY KEY) when an INSERT omits it: ``<table>_<column>_rowid_seq``,
    made of [a-z0-9_] only (a short hash keeps names that needed
    changing, or that are long, unique)."""
    base = f'{table}_{column}'.lower()
    safe = re.sub(r'[^a-z0-9_]', '_', base)
    if safe != f'{table}_{column}' or len(safe) > 40 or safe[:1].isdigit():
        import hashlib
        digest = hashlib.sha1(f'{table}\x00{column}'.encode('utf-8')).hexdigest()[:8]
        safe = f'r_{safe[:30]}_{digest}'
    return safe + ROWID_SEQUENCE_SUFFIX


def _matching_paren(sql: str, open_pos: int) -> int:
    """Index of the ')' closing the '(' at ``open_pos`` (-1 if none),
    ignoring parentheses inside strings and comments."""
    depth = 0
    for start, end in code_spans(sql):
        if end <= open_pos:
            continue
        for i in range(max(start, open_pos), end):
            ch = sql[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
    return -1


def _top_level_commas(sql: str, start: int, end: int) -> List[int]:
    commas = []
    depth = 0
    for s, e in code_spans(sql):
        for i in range(max(s, start), min(e, end)):
            ch = sql[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                commas.append(i)
    return commas


class _ColumnDef(NamedTuple):
    name: str               # engine name
    decltype: str           # declared type as written ('' when none)
    type_name: str          # upper-cased type words without arguments
    type_start: int
    rest_start: int


def _parse_column_def(text: str) -> Optional[_ColumnDef]:
    m = _NAME_RE.match(text)
    if not m or m.group(1).upper() in _TABLE_CONSTRAINT_WORDS:
        return None
    pos = m.end()
    words = []
    type_start = type_end = None
    while True:
        w = _TYPE_WORD_RE.match(text, pos)
        if not w or w.group(1).upper() in _COLUMN_CONSTRAINT_WORDS:
            break
        if type_start is None:
            type_start = w.start(1)
        words.append(w.group(1).upper())
        pos = type_end = w.end()
    name = identifier_name(m.group(1))
    if type_start is None:
        return _ColumnDef(name, '', '', m.end(), m.end())
    # Skip type arguments, e.g. DECIMAL(10, 2) or VARCHAR(20), and [] suffixes
    rest_start = type_end
    after = text[type_end:]
    stripped = after.lstrip()
    if stripped.startswith('('):
        close = _matching_paren(text, type_end + (len(after) - len(stripped)))
        if close > 0:
            rest_start = close + 1
    while text[rest_start:].lstrip().startswith('[]'):
        rest_start = text.index('[]', rest_start) + 2
    return _ColumnDef(name, text[type_start:rest_start].strip(), ' '.join(words),
                      type_start, rest_start)


def _affinity_type(type_name: str) -> str:
    """HeliosDB type for a type name HeliosDB does not know, by SQLite's
    column affinity rules (https://sqlite.org/datatype3.html#affinity)."""
    if 'INT' in type_name:
        return 'BIGINT'
    if 'CHAR' in type_name or 'CLOB' in type_name or 'TEXT' in type_name:
        return 'TEXT'
    if 'BLOB' in type_name:
        return 'BYTEA'
    if 'REAL' in type_name or 'FLOA' in type_name or 'DOUB' in type_name:
        return 'DOUBLE PRECISION'
    return 'TEXT'


def _rewrite_column_def(text: str, rowid_default: Optional[str] = None) -> str:
    """Map the type of one column definition (``name type constraints``).
    ``rowid_default`` is the DEFAULT expression for the table's rowid
    alias column, which this definition is when it is ``INTEGER PRIMARY
    KEY`` (or ``rowid_default`` is given for a table-level key)."""
    col = _parse_column_def(text)
    if col is None or not col.type_name:
        return text
    type_name = col.type_name
    rest = text[col.rest_start:]
    if type_name in _INTEGER_TYPES:
        new_type = 'BIGINT'
        if type_name in ('INT', 'INTEGER') and (rowid_default or _PRIMARY_KEY_RE.search(rest)):
            rest = _AUTOINCREMENT_RE.sub('', rest)
            if rowid_default and not _DEFAULT_RE.search(rest):
                rest = rest.rstrip() + f' DEFAULT {rowid_default}'
                trailing = text[len(text.rstrip()):]
                rest += trailing
    elif type_name in _TYPE_MAP:
        new_type = _TYPE_MAP[type_name]
    elif type_name.split()[0] in _ENGINE_TYPES or type_name.startswith('TIMESTAMP') \
            or type_name.startswith('TIME ') or type_name.startswith('CHARACTER'):
        return text
    else:
        new_type = _affinity_type(type_name)
        if col.decltype.endswith('[]'):
            return text
    return text[:col.type_start] + new_type + rest


class CreateTable(NamedTuple):
    table: str                         # engine name of the table
    if_not_exists: bool
    columns: List[Tuple[str, str]]     # (engine column name, declared type)
    rowid_column: Optional[str]        # the INTEGER PRIMARY KEY column


def _create_table_parts(sql: str):
    m = _CREATE_TABLE_RE.match(sql)
    if not m:
        return None
    open_pos = m.end() - 1
    close = _matching_paren(sql, open_pos)
    if close < 0:
        return None
    bounds = [open_pos] + _top_level_commas(sql, open_pos + 1, close) + [close]
    return m, open_pos, close, bounds


def parse_create_table(sql: str) -> Optional[CreateTable]:
    """Table name, declared column types and rowid alias column of a
    ``CREATE TABLE name (...)`` statement (None for any other statement,
    including ``CREATE TABLE ... AS SELECT``)."""
    if first_keyword(sql) != 'CREATE':
        return None
    parts = _create_table_parts(sql)
    if parts is None:
        return None
    m, open_pos, close, bounds = parts
    table = identifier_name(m.group('n2') or m.group('n1'))
    columns: List[Tuple[str, str]] = []
    rowid = None
    int_columns = {}
    table_pk: List[str] = []
    for a, b in zip(bounds, bounds[1:]):
        text = sql[a + 1:b]
        pk = _TABLE_PK_RE.match(text)
        if pk:
            table_pk.append(identifier_name(pk.group(1)))
            continue
        col = _parse_column_def(text)
        if col is None:
            continue
        columns.append((col.name, col.decltype))
        if col.type_name in ('INT', 'INTEGER'):
            int_columns[col.name] = True
            if rowid is None and _PRIMARY_KEY_RE.search(text[col.rest_start:]):
                rowid = col.name
    if rowid is None and len(table_pk) == 1 and table_pk[0] in int_columns:
        rowid = table_pk[0]
    return CreateTable(table, bool(m.group('ine')), columns, rowid)


def rewrite_ddl_types(sql: str) -> str:
    """Map SQLite column types to HeliosDB types in ``CREATE TABLE (...)``
    and ``ALTER TABLE ... ADD [COLUMN]`` (see the table above). Every other
    statement is returned unchanged."""
    keyword = first_keyword(sql)
    if keyword == 'CREATE':
        info = parse_create_table(sql)
        parts = _create_table_parts(sql) if info is not None else None
        if parts is None:
            return sql
        m, open_pos, close, bounds = parts
        rowid_default = None
        if info.rowid_column is not None:
            rowid_default = f"nextval('{rowid_sequence_name(info.table, info.rowid_column)}')"
        out = [sql[:open_pos + 1]]
        for a, b in zip(bounds, bounds[1:]):
            text = sql[a + 1:b]
            col = _parse_column_def(text)
            is_rowid = col is not None and col.name == info.rowid_column
            out.append(_rewrite_column_def(text, rowid_default if is_rowid else None))
            out.append(sql[b])
        out.append(sql[close + 1:])
        return ''.join(out)
    if keyword == 'ALTER':
        m = _ALTER_ADD_RE.match(sql)
        if not m:
            return sql
        return sql[:m.end()] + _rewrite_column_def(sql[m.end():])
    return sql


# --------------------------------------------------------------------------
# INSERT / DROP / ALTER parsing (rowid assignment and declared types)
# --------------------------------------------------------------------------

_INSERT_HEAD_RE = re.compile(
    r'\s*(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+(?P<n1>' + _IDENT + r')'
    r'(?:\s*\.\s*(?P<n2>' + _IDENT + r'))?'
    r'(?:\s+AS\s+' + _IDENT + r')?\s*',
    re.IGNORECASE,
)
_VALUES_RE = re.compile(r'VALUES\b\s*', re.IGNORECASE)
_DEFAULT_VALUES_RE = re.compile(r'DEFAULT\s+VALUES\b', re.IGNORECASE)


class InsertInfo(NamedTuple):
    table: str
    columns: Optional[List[str]]                    # None: no column list
    rows: Optional[List[List[Tuple[int, int]]]]     # VALUES expressions (spans); None otherwise
    default_values: bool


def parse_insert(sql: str) -> Optional[InsertInfo]:
    """Target table, column list and the spans of each ``VALUES`` row's
    expressions of an INSERT (None when the statement is not one)."""
    m = _INSERT_HEAD_RE.match(sql)
    if not m:
        return None
    table = identifier_name(m.group('n2') or m.group('n1'))
    pos = m.end()
    columns = None
    if sql.startswith('(', pos):
        close = _matching_paren(sql, pos)
        if close < 0:
            return None
        inner = sql[pos + 1:close]
        # '(SELECT ...)' / '(VALUES ...)' would be a query, not a column list
        if first_keyword(inner) in ('SELECT', 'WITH', 'VALUES'):
            return InsertInfo(table, None, None, False)
        bounds = [pos] + _top_level_commas(sql, pos + 1, close) + [close]
        columns = [identifier_name(sql[a + 1:b]) for a, b in zip(bounds, bounds[1:])]
        pos = close + 1
        while pos < len(sql) and sql[pos].isspace():
            pos += 1
    if _DEFAULT_VALUES_RE.match(sql, pos):
        return InsertInfo(table, columns, None, True)
    v = _VALUES_RE.match(sql, pos)
    if not v:
        return InsertInfo(table, columns, None, False)
    pos = v.end()
    rows: List[List[Tuple[int, int]]] = []
    while pos < len(sql) and sql[pos] == '(':
        close = _matching_paren(sql, pos)
        if close < 0:
            return InsertInfo(table, columns, None, False)
        bounds = [pos] + _top_level_commas(sql, pos + 1, close) + [close]
        rows.append([(a + 1, b) for a, b in zip(bounds, bounds[1:])])
        pos = close + 1
        while pos < len(sql) and sql[pos].isspace():
            pos += 1
        if pos < len(sql) and sql[pos] == ',':
            pos += 1
            while pos < len(sql) and sql[pos].isspace():
                pos += 1
    return InsertInfo(table, columns, rows, False)


_DROP_TABLE_RE = re.compile(r'\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<names>.+?)'
                            r'(?:\s+(?:CASCADE|RESTRICT))?\s*;?\s*$',
                            re.IGNORECASE | re.DOTALL)


def dropped_tables(sql: str) -> List[str]:
    """Engine names of the tables a ``DROP TABLE`` removes."""
    if first_keyword(sql) != 'DROP':
        return []
    m = _DROP_TABLE_RE.match(sql)
    if not m:
        return []
    names = []
    for part in m.group('names').split(','):
        part = part.strip()
        if part:
            names.append(identifier_name(re.split(r'\s*\.\s*', part)[-1]))
    return names


_ALTER_RE = re.compile(
    r'\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<n1>' + _IDENT + r')(?:\s*\.\s*(?P<n2>' + _IDENT + r'))?'
    r'\s+(?P<action>.*)$', re.IGNORECASE | re.DOTALL)
_RENAME_TABLE_RE = re.compile(r'RENAME\s+TO\s+(' + _IDENT + r')', re.IGNORECASE)
_RENAME_COLUMN_RE = re.compile(r'RENAME\s+(?:COLUMN\s+)?(' + _IDENT + r')\s+TO\s+(' + _IDENT + r')',
                               re.IGNORECASE)
_DROP_COLUMN_RE = re.compile(r'DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(' + _IDENT + r')',
                             re.IGNORECASE)
_ADD_COLUMN_RE = re.compile(r'ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(.*)$',
                            re.IGNORECASE | re.DOTALL)


def parse_alter_table(sql: str) -> Optional[Tuple[str, str, Tuple[str, ...]]]:
    """``(table, action, args)`` for the ALTER TABLE forms that change
    declared column types: ('rename_table', (new,)), ('rename_column',
    (old, new)), ('drop_column', (name,)), ('add_column', (name, decltype))."""
    if first_keyword(sql) != 'ALTER':
        return None
    m = _ALTER_RE.match(sql)
    if not m:
        return None
    table = identifier_name(m.group('n2') or m.group('n1'))
    action = m.group('action').rstrip().rstrip(';')
    r = _RENAME_TABLE_RE.match(action)
    if r:
        return table, 'rename_table', (identifier_name(r.group(1)),)
    r = _RENAME_COLUMN_RE.match(action)
    if r:
        return table, 'rename_column', (identifier_name(r.group(1)), identifier_name(r.group(2)))
    r = _DROP_COLUMN_RE.match(action)
    if r:
        return table, 'drop_column', (identifier_name(r.group(1)),)
    r = _ADD_COLUMN_RE.match(action)
    if r:
        col = _parse_column_def(r.group(1))
        if col is not None:
            return table, 'add_column', (col.name, col.decltype)
    return None


_TABLE_REF_RE = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE)\s+((?:"[^"]+"|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][\w$]*))?)'
    r'(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?'
    r'((?:\s*,\s*(?:"[^"]+"|[A-Za-z_][\w$.]*)(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?)*)',
    re.IGNORECASE,
)
_NOT_TABLES = frozenset(('SELECT', 'LATERAL', 'ONLY', 'UNNEST'))


def _table_name(ref: str) -> Optional[str]:
    ref = ref.strip()
    if not ref or ref.startswith('('):
        return None
    last = re.split(r'\s*\.\s*', ref)[-1]
    if last.startswith('"') and last.endswith('"'):
        return last[1:-1]
    if last.upper() in _NOT_TABLES:
        return None
    return last.lower()


def referenced_tables(sql: str) -> List[str]:
    """Best-effort list of table names a statement reads or writes (after
    FROM / JOIN / INTO / UPDATE, including comma-separated FROM lists).
    Unquoted names are lower-cased as the engine folds them."""
    code = ' '.join(sql[s:e] for s, e in code_spans(sql, keep_identifiers=True))
    tables: List[str] = []
    for m in _TABLE_REF_RE.finditer(code):
        names = [m.group(1)]
        if m.group(2):
            for part in m.group(2).split(','):
                part = part.strip()
                if part:
                    names.append(part.split()[0])
        for ref in names:
            name = _table_name(ref)
            if name and name not in tables:
                tables.append(name)
    return tables
