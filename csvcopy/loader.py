"""COPY FROM into a table: NULL handling and type input.

A table: {"columns": [{"name": "id", "type": "int"}, {"name": "note", "type": "text"}, ...]}
Types: int, numeric (kept as a string with the input digits), text, bool.

The NULL / empty-string rules are exactly CopyReadAttributesCSV's:

* an *unquoted* field whose text equals the NULL string is stored as NULL,
  unless the column is listed in FORCE_NOT_NULL, in which case the text is
  kept as-is;
* FORCE_NULL turns a matching field into NULL even when it was quoted
  (FORCE_NULL wins over FORCE_NOT_NULL, and the two may not name the same
  column);
* any other field, including a quoted empty string, is the literal text.

Field-count and parse errors carry the server's wording, including the
physical line number on which the offending record ends.
"""
import re

from .options import CopyError, resolve
from .parser import parse

# How the simplified type names in the job files appear in server errors.
_PG_TYPE = {"int": "integer", "numeric": "numeric", "text": "text", "bool": "boolean"}

_INT_RE = re.compile(r"[ ]*[+-]?[0-9]+$")
_NUMERIC_RE = re.compile(
    r"[ ]*[+-]?(?:"
    r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    r"|nan|infinity)"
    r"[ ]*$",
    re.IGNORECASE,
)
_BOOL_IN = {
    "t": True, "true": True, "y": True, "yes": True, "on": True, "1": True,
    "f": False, "false": False, "n": False, "no": False, "off": False, "0": False,
}

# Filled in by copy_from so error context names the target table.
_table_name = "<table>"


def _cast(value, typ, col, line):
    if value is None or typ == "text":
        return value
    pg_type = _PG_TYPE.get(typ, typ)
    try:
        if typ == "int":
            if not _INT_RE.match(value):
                raise ValueError
            return int(value)
        if typ == "numeric":
            if not _NUMERIC_RE.match(value):
                raise ValueError
            return value
        if typ == "bool":
            return _BOOL_IN[value.strip().lower()]
    except (ValueError, KeyError):
        raise CopyError(
            f'invalid input syntax for type {pg_type}: "{value}"'
            f"\nCONTEXT:  COPY {_table_name}, line {line}, column {col}"
        )
    raise CopyError(f'unknown type "{typ}" for column "{col}"')


def copy_from(table, data, options=None):
    global _table_name
    cols = [c["name"] for c in table["columns"]]
    types = {c["name"]: c["type"] for c in table["columns"]}
    _table_name = table.get("name", "<table>")
    opts = resolve(options, cols)
    null = opts["null"]
    force_null = set(opts["force_null"])
    force_not_null = set(opts["force_not_null"])

    out = []
    record_no = 0
    for fields, end_line in parse(data, opts):
        record_no += 1
        if opts["header"] and record_no == 1:
            continue

        if len(fields) < len(cols):
            missing = cols[len(fields)]
            raise CopyError(
                f'missing data for column "{missing}"'
                f"\nCONTEXT:  COPY {_table_name}, line {end_line}"
            )
        if len(fields) > len(cols):
            raise CopyError(
                "extra data after last expected column"
                f"\nCONTEXT:  COPY {_table_name}, line {end_line}"
            )

        row = {}
        for col, (text, quoted) in zip(cols, fields):
            if text == null:
                if col in force_null:
                    value = None
                elif col in force_not_null:
                    value = text
                else:
                    value = None if not quoted else text
            else:
                value = text
            row[col] = _cast(value, types[col], col, end_line)
        out.append(row)
    return out

