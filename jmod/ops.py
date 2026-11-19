"""jsonb modification operators and functions (PostgreSQL names).

  a || b                     concatenate / merge
  a - key | a - index | a - keys[]   delete
  a #- path                  delete at path
  jsonb_set(target, path, new, create_missing=true)
  jsonb_insert(target, path, new, insert_after=false)
  jsonb_strip_nulls(a)
  jsonb_array_length(a), jsonb_typeof(a)
Paths are lists of text elements; array positions are given as integer text.
"""
import copy

from .value import JsonbError


def concat(a, b):
    if a is None or b is None:
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        out.update(b)
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    if isinstance(a, list):
        return a + [b]
    raise JsonbError("invalid concatenation of jsonb objects")


def delete(a, what):
    if a is None or what is None:
        return None
    if isinstance(what, list):
        out = a
        for k in what:
            out = delete(out, k)
        return out
    if isinstance(a, dict):
        return {k: v for k, v in a.items() if k != str(what)}
    if isinstance(a, list):
        if isinstance(what, int):
            return [x for i, x in enumerate(a) if i != what]
        return [x for x in a if x != what]
    return a


def _walk(doc, path):
    cur = doc
    for p in path[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur


def delete_path(a, path):
    if a is None or path is None:
        return None
    out = copy.deepcopy(a)
    try:
        parent = _walk(out, path)
        last = path[-1]
        if isinstance(parent, list):
            del parent[int(last)]
        else:
            del parent[last]
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    return out


def jsonb_set(target, path, new, create_missing=True):
    out = copy.deepcopy(target)
    parent = _walk(out, path)
    last = path[-1]
    if isinstance(parent, list):
        i = int(last)
        if i < len(parent):
            parent[i] = new
        elif create_missing:
            parent.append(new)
    else:
        if last in parent or create_missing:
            parent[last] = new
    return out


def jsonb_insert(target, path, new, insert_after=False):
    out = copy.deepcopy(target)
    parent = _walk(out, path)
    last = path[-1]
    if isinstance(parent, list):
        i = int(last) + (1 if insert_after else 0)
        parent.insert(i, new)
    else:
        parent[last] = new
    return out


def strip_nulls(a):
    if isinstance(a, dict):
        return {k: strip_nulls(v) for k, v in a.items() if v is not None}
    if isinstance(a, list):
        return [strip_nulls(v) for v in a if v is not None]
    return a


def array_length(a):
    return len(a) if isinstance(a, list) else 0


def typeof(a):
    from decimal import Decimal
    if isinstance(a, dict):
        return "object"
    if isinstance(a, list):
        return "array"
    if isinstance(a, bool):
        return "boolean"
    if a is None:
        return "null"
    if isinstance(a, Decimal):
        return "number"
    return "string"
