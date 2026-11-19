"""COPY FROM into a table: NULL handling and type input.

A table: {"columns": [{"name": "id", "type": "int"}, {"name": "note", "type": "text"}, ...]}
Types: int, numeric (kept as a string with the input digits), text, bool.
"""
from .options import resolve
from .parser import records


class CopyError(Exception):
    pass


def _cast(value, typ, col, line):
    if value is None or typ == "text":
        return value
    try:
        if typ == "int":
            return int(value)
        if typ == "numeric":
            float(value)
            return value
        if typ == "bool":
            return {"t": True, "true": True, "f": False, "false": False}[value.lower()]
    except (ValueError, KeyError):
        raise CopyError(f'invalid input syntax for type {typ}: "{value}" (line {line}, column {col})')
    raise CopyError(f"unknown type {typ}")


def copy_from(table, data, options=None):
    opts = resolve(options)
    cols = [c["name"] for c in table["columns"]]
    types = {c["name"]: c["type"] for c in table["columns"]}
    out = []
    for n, fields in enumerate(records(data, opts), 1):
        if opts["header"] and n == 1:
            continue
        row = {}
        for col, (text, quoted) in zip(cols, fields):
            value = None if text == opts["null"] else text
            row[col] = _cast(value, types[col], col, n)
        out.append(row)
    return out
