"""Result-value typing for heliosdb_sqlite.

HeliosDB Nano describes every result column in the PostgreSQL wire protocol's
RowDescription message, which carries the column's type OID (``pg_type.oid``).
Database drivers expose that OID as ``cursor.description[i].type_code``. This
module turns a column value, received in the protocol's text format, into the
Python value the standard library ``sqlite3`` module would return for the same
data:

==========================================  ===========================
Server column type                          Python value
==========================================  ===========================
SMALLINT, INTEGER, BIGINT (int2/int4/int8)  ``int``
REAL, DOUBLE PRECISION (float4/float8)      ``float``
NUMERIC / DECIMAL                           ``int`` when integral and
                                            within 64 bits, else
                                            ``float`` (SQLite NUMERIC
                                            affinity)
BOOLEAN                                     ``int`` 1 / 0 (SQLite has
                                            no boolean type)
BYTEA                                       ``bytes``
NULL (any type)                             ``None``
TIMESTAMP, TIMESTAMPTZ                      ``str``, with the fraction's
                                            trailing zeros removed
TEXT, VARCHAR, CHAR, JSON, UUID, DATE,      ``str``, exactly as the
TIME, VECTOR, arrays, any other type        server sent it (SQLite
                                            stores these as TEXT)
==========================================  ===========================

The mapping is driven only by the type OID, never by the shape of the value,
so the text ``'7'`` in a TEXT column stays ``'7'`` and ``'NULL'`` stays a
string.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

# Type OIDs from PostgreSQL's pg_type catalog, as sent in RowDescription.
BOOL = 16
BYTEA = 17
CHAR = 18
NAME = 19
INT8 = 20
INT2 = 21
INT4 = 23
TEXT = 25
OID = 26
JSON = 114
XML = 142
FLOAT4 = 700
FLOAT8 = 701
UNKNOWN = 705
BPCHAR = 1042
VARCHAR = 1043
DATE = 1082
TIME = 1083
TIMESTAMP = 1114
TIMESTAMPTZ = 1184
INTERVAL = 1186
TIMETZ = 1266
NUMERIC = 1700
UUID = 2950
JSONB = 3802

_INT64_MIN = -(2 ** 63)
_INT64_MAX = 2 ** 63 - 1

_TRUE_TEXT = frozenset(('t', 'true', 'y', 'yes', 'on', '1'))
_FALSE_TEXT = frozenset(('f', 'false', 'n', 'no', 'off', '0'))


def _as_text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode('utf-8')
    return str(value)


def _to_int(value: Any) -> int:
    if isinstance(value, int):  # includes bool from a decoding driver
        return int(value)
    return int(_as_text(value).strip())


def _to_float(value: Any) -> float:
    if isinstance(value, float):
        return value
    # float() accepts every spelling the server uses: '1.5', '1e-7', 'NaN',
    # 'Infinity', 'inf', '-inf'.
    return float(value if isinstance(value, (int, Decimal)) else _as_text(value).strip())


def _to_numeric(value: Any) -> Any:
    """NUMERIC follows SQLite's NUMERIC affinity: an integral value that fits
    in 64 bits becomes ``int``, anything else ``float``."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value if _INT64_MIN <= value <= _INT64_MAX else float(value)
    try:
        dec = value if isinstance(value, Decimal) else Decimal(_as_text(value).strip())
    except InvalidOperation:
        raise ValueError(f"invalid NUMERIC value {value!r}") from None
    if dec.is_finite() and dec == dec.to_integral_value():
        as_int = int(dec)
        if _INT64_MIN <= as_int <= _INT64_MAX:
            return as_int
    return float(dec)


def _to_bool(value: Any) -> int:
    if isinstance(value, (bool, int)):
        return 1 if value else 0
    text = _as_text(value).strip().lower()
    if text in _TRUE_TEXT:
        return 1
    if text in _FALSE_TEXT:
        return 0
    raise ValueError(f"invalid BOOLEAN value {value!r}")


def _to_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    text = str(value)
    if text.startswith('\\x'):
        # bytea_output = hex (the default): \x followed by two hex digits/byte
        return bytes.fromhex(text[2:])
    # bytea_output = escape: printable ASCII as-is, '\\' for a backslash and
    # '\ooo' (octal) for any other byte.
    out = bytearray()
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch != '\\':
            out += ch.encode('latin-1')
            i += 1
        elif text.startswith('\\\\', i):
            out.append(0x5C)
            i += 2
        else:
            octal = text[i + 1:i + 4]
            if len(octal) != 3 or any(c not in '01234567' for c in octal):
                raise ValueError(f"invalid BYTEA value {value!r}")
            out.append(int(octal, 8))
            i += 4
    return bytes(out)


_FRACTION_RE = re.compile(r'(\d{2}:\d{2}:\d{2})\.(\d+)')


def _to_timestamp_text(value: Any) -> str:
    """TIMESTAMP text without the trailing zeros the server pads the
    fraction with: '2026-10-07 12:34:56.000000' -> '2026-10-07 12:34:56',
    '... 12:34:56.500000' -> '... 12:34:56.5' (the in-process binding
    renders timestamps the same way)."""
    text = _as_text(value)

    def trim(m: 're.Match') -> str:
        frac = m.group(2).rstrip('0')
        return m.group(1) + ('.' + frac if frac else '')

    return _FRACTION_RE.sub(trim, text, count=1)


def _to_text(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode('utf-8')
    return value


# OID -> converter. Any OID not listed (TEXT, VARCHAR, DATE, UUID, JSON,
# VECTOR, arrays, ...) is returned as the text the server sent.
_CONVERTERS: Dict[int, Callable[[Any], Any]] = {
    INT2: _to_int,
    INT4: _to_int,
    INT8: _to_int,
    OID: _to_int,
    FLOAT4: _to_float,
    FLOAT8: _to_float,
    NUMERIC: _to_numeric,
    BOOL: _to_bool,
    BYTEA: _to_bytes,
    TIMESTAMP: _to_timestamp_text,
    TIMESTAMPTZ: _to_timestamp_text,
}

# OID -> type names tried, in order, against converters registered with
# register_converter() when a connection uses detect_types=PARSE_DECLTYPES.
# The first name is the SQL spelling a CREATE TABLE would normally use, the
# others are PostgreSQL's internal name and the closest SQLite spelling.
TYPE_NAMES: Dict[int, Tuple[str, ...]] = {
    BOOL: ('BOOLEAN', 'BOOL'),
    BYTEA: ('BYTEA', 'BLOB'),
    CHAR: ('CHAR',),
    NAME: ('NAME',),
    INT8: ('BIGINT', 'INT8'),
    INT2: ('SMALLINT', 'INT2'),
    INT4: ('INTEGER', 'INT', 'INT4'),
    TEXT: ('TEXT',),
    OID: ('OID',),
    JSON: ('JSON',),
    XML: ('XML',),
    FLOAT4: ('REAL', 'FLOAT4'),
    FLOAT8: ('DOUBLE', 'FLOAT8'),
    BPCHAR: ('CHAR', 'BPCHAR'),
    VARCHAR: ('VARCHAR',),
    DATE: ('DATE',),
    TIME: ('TIME',),
    TIMESTAMP: ('TIMESTAMP', 'DATETIME'),
    TIMESTAMPTZ: ('TIMESTAMPTZ',),
    INTERVAL: ('INTERVAL',),
    TIMETZ: ('TIMETZ',),
    NUMERIC: ('NUMERIC', 'DECIMAL'),
    UUID: ('UUID',),
    JSONB: ('JSONB',),
}


def convert_value(type_oid: Optional[int], value: Any) -> Any:
    """Convert one column value to its sqlite3-compatible Python value.

    ``type_oid`` is the column's type OID from the server's RowDescription.
    ``None`` (no type information) returns the value unchanged.

    Raises:
        ValueError: the value is not valid text for its declared type.
    """
    if value is None or type_oid is None:
        return value
    converter = _CONVERTERS.get(type_oid)
    if converter is None:
        return _to_text(value)
    try:
        return converter(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot convert {value!r} for column type OID {type_oid}: {exc}") from None


def convert_row(type_oids: Optional[Sequence[Optional[int]]], row: Sequence[Any]) -> list:
    """Convert every value of ``row`` using the matching entry of ``type_oids``."""
    if not type_oids:
        return list(row)
    return [
        convert_value(type_oids[i] if i < len(type_oids) else None, value)
        for i, value in enumerate(row)
    ]


def converter_input(type_oid: Optional[int], value: Any) -> bytes:
    """The ``bytes`` handed to a register_converter() callable, as sqlite3
    does: the stored bytes for BYTEA, otherwise the value's text in UTF-8."""
    if type_oid == BYTEA:
        return _to_bytes(value)
    if type_oid == BOOL:
        return b'1' if _to_bool(value) else b'0'  # SQLite stores booleans as 1 / 0
    if type_oid in (TIMESTAMP, TIMESTAMPTZ):
        return _to_timestamp_text(value).encode('utf-8')
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return str(value).encode('utf-8')
