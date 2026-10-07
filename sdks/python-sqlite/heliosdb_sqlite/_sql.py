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
# whatever the declared type name says, and ``INTEGER PRIMARY KEY`` is an
# alias for the rowid that is assigned automatically. HeliosDB follows
# PostgreSQL (INTEGER is 32-bit, REAL is 4-byte) and does not accept BLOB,
# DATETIME or TINYINT. So that schemas written for SQLite keep their meaning,
# column types are mapped as follows:
#
#   INT, INTEGER, TINYINT, SMALLINT, MEDIUMINT, BIGINT, INT2, INT8,
#   UNSIGNED BIG INT                       -> BIGINT
#   INTEGER PRIMARY KEY / INT PRIMARY KEY  -> INTEGER PRIMARY KEY AUTOINCREMENT
#                                             (64-bit, assigned when omitted)
#   REAL, FLOAT, DOUBLE                    -> DOUBLE PRECISION
#   BLOB                                   -> BYTEA
#   DATETIME                               -> TIMESTAMP
#
# Every other type (TEXT, VARCHAR(n), NUMERIC(p,s), BOOLEAN, DATE, VECTOR(n),
# ...) is left as written.

_INTEGER_TYPES = frozenset((
    'INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT', 'INT2', 'INT8',
    'UNSIGNED BIG INT',
))
_TYPE_MAP = {
    'REAL': 'DOUBLE PRECISION',
    'FLOAT': 'DOUBLE PRECISION',
    'DOUBLE': 'DOUBLE PRECISION',
    'BLOB': 'BYTEA',
    'DATETIME': 'TIMESTAMP',
}
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
    r'(?:IF\s+NOT\s+EXISTS\s+)?' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?\s*\(',
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
_AUTOINCREMENT_RE = re.compile(r'\bAUTOINCREMENT\b', re.IGNORECASE)


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


def _rewrite_column_def(text: str) -> str:
    """Map the type of one column definition (``name type constraints``)."""
    m = _NAME_RE.match(text)
    if not m or m.group(1).upper() in _TABLE_CONSTRAINT_WORDS:
        return text
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
    if type_start is None:
        return text
    # Skip type arguments, e.g. DECIMAL(10, 2) or VARCHAR(20)
    rest_start = type_end
    after = text[type_end:]
    stripped = after.lstrip()
    if stripped.startswith('('):
        close = _matching_paren(text, type_end + (len(after) - len(stripped)))
        if close > 0:
            rest_start = close + 1
    type_name = ' '.join(words)
    rest = text[rest_start:]
    if type_name in _INTEGER_TYPES:
        if type_name in ('INT', 'INTEGER') and _PRIMARY_KEY_RE.search(rest):
            new_type = 'INTEGER'
            if not _AUTOINCREMENT_RE.search(rest):
                rest = _PRIMARY_KEY_RE.sub('PRIMARY KEY AUTOINCREMENT', rest, count=1)
        else:
            new_type = 'BIGINT'
    elif type_name in _TYPE_MAP:
        new_type = _TYPE_MAP[type_name]
    else:
        return text
    return text[:type_start] + new_type + rest


def rewrite_ddl_types(sql: str) -> str:
    """Map SQLite column types to HeliosDB types in ``CREATE TABLE (...)``
    and ``ALTER TABLE ... ADD [COLUMN]`` (see the table above). Every other
    statement is returned unchanged."""
    keyword = first_keyword(sql)
    if keyword == 'CREATE':
        m = _CREATE_TABLE_RE.match(sql)
        if not m:
            return sql
        open_pos = m.end() - 1
        close = _matching_paren(sql, open_pos)
        if close < 0:
            return sql
        bounds = [open_pos] + _top_level_commas(sql, open_pos + 1, close) + [close]
        out = [sql[:open_pos + 1]]
        for a, b in zip(bounds, bounds[1:]):
            out.append(_rewrite_column_def(sql[a + 1:b]))
            out.append(sql[b])
        out.append(sql[close + 1:])
        return ''.join(out)
    if keyword == 'ALTER':
        m = _ALTER_ADD_RE.match(sql)
        if not m:
            return sql
        return sql[:m.end()] + _rewrite_column_def(sql[m.end():])
    return sql


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
