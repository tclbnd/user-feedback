"""jsonb modification operators and functions (PostgreSQL semantics).

  a || b                     concatenate / merge
  a - key | a - index | a - keys[]   delete
  a #- path                  delete at path
  jsonb_set(target, path, new, create_missing=true)
  jsonb_insert(target, path, new, insert_after=false)
  jsonb_strip_nulls(a)
  jsonb_array_length(a), jsonb_typeof(a)

Values are in the parsed form from jmod.value: JSON null is the JSON_NULL
sentinel, SQL NULL is None.  Like PostgreSQL, every function is strict
(any SQL NULL argument yields SQL NULL) and a path containing a null
element yields SQL NULL.  Where PostgreSQL raises an error, JsonbError
is raised with PostgreSQL's message.

Paths are lists of text elements; array positions are given as integer
text.  Negative array positions count from the end of the array.
"""
import copy
from decimal import Decimal

from .value import JSON_NULL, JsonbError

_INT_MIN = -2147483648
_INT_MAX = 2147483647


# ------------------------------ || ------------------------------

def concat(a, b):
    if a is None or b is None:
        return None
    if isinstance(a, list):
        if isinstance(b, list):
            return a + b
        # any non-array (including an object) is appended as one element
        return a + [b]
    if isinstance(b, list):
        # any non-array is prepended as one element
        return [a] + b
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        out.update(b)
        return out
    raise JsonbError("invalid concatenation of jsonb objects")


# ------------------------------ - ------------------------------

def delete(a, what):
    if a is None or what is None:
        return None
    if isinstance(what, list):
        return _delete_keys(a, what)
    if isinstance(what, bool):
        raise JsonbError("operator does not exist: jsonb - boolean")
    if isinstance(what, int):
        return _delete_index(a, what)
    if isinstance(what, str):
        return _delete_key(a, what)
    raise JsonbError("operator does not exist: jsonb - numeric")


def _delete_key(a, key):
    # jsonb - text
    if isinstance(a, dict):
        return {k: v for k, v in a.items() if k != key}
    if isinstance(a, list):
        # deletes all matching string values from the array
        return [x for x in a if not (isinstance(x, str) and x == key)]
    raise JsonbError("cannot delete from scalar")


def _delete_keys(a, keys):
    # jsonb - text[]
    if not isinstance(a, (dict, list)):
        raise JsonbError("cannot delete from scalar")
    out = a
    for k in keys:
        if k is None:
            continue  # nulls in the key array are ignored
        out = _delete_key(out, _text(k))
    return out


def _delete_index(a, idx):
    # jsonb - integer
    if not _INT_MIN <= idx <= _INT_MAX:
        raise JsonbError("operator does not exist: jsonb - bigint")
    if isinstance(a, dict):
        raise JsonbError("cannot delete from object using integer")
    if not isinstance(a, list):
        raise JsonbError("cannot delete from scalar")
    n = len(a)
    if idx < 0:
        idx += n
    if idx < 0 or idx >= n:
        return list(a)
    return a[:idx] + a[idx + 1:]


def _text(k):
    if isinstance(k, str):
        return k
    if isinstance(k, int) and not isinstance(k, bool):
        return str(k)
    raise JsonbError("path element must be a string")


# ------------------------------ paths ------------------------------

def _coerce_path(path):
    if not isinstance(path, list):
        raise JsonbError("malformed array literal")
    out = []
    for p in path:
        if p is None:
            return None  # a null path element makes the result SQL NULL
        out.append(_text(p))
    return out


def _parse_index(s, level):
    """Parse a path element as an array index, as PostgreSQL's
    setPathArray does with strtol: leading whitespace and a sign are
    accepted, anything else that remains is an error."""
    n = len(s)
    i = 0
    while i < n and s[i] in " \t\n\v\f\r":
        i += 1
    j = i
    if j < n and s[j] in "+-":
        j += 1
    k = j
    while k < n and "0" <= s[k] <= "9":
        k += 1
    if k == j or k != n:
        raise JsonbError('path element at position %d is not an integer: "%s"'
                         % (level + 1, s))
    v = int(s[i:])
    if v < _INT_MIN or v > _INT_MAX:
        raise JsonbError('path element at position %d is not an integer: "%s"'
                         % (level + 1, s))
    return v


# ------------------------------ #- ------------------------------

def delete_path(a, path):
    if a is None or path is None:
        return None
    path = _coerce_path(path)
    if path is None:
        return None
    if len(path) == 0:
        return a
    return _delete_at(copy.deepcopy(a), path, 0)


def _delete_at(node, path, level):
    if isinstance(node, dict):
        key = path[level]
        if level == len(path) - 1:
            node.pop(key, None)
        elif key in node:
            node[key] = _delete_at(node[key], path, level + 1)
    elif isinstance(node, list):
        idx = _parse_index(path[level], level)
        n = len(node)
        if idx < 0:
            idx += n
        if 0 <= idx < n:
            if level == len(path) - 1:
                del node[idx]
            else:
                node[idx] = _delete_at(node[idx], path, level + 1)
    # anything else (scalar, missing key, out-of-range index) is a no-op
    return node


# ------------------------------ jsonb_set / jsonb_insert ------------------------------

def jsonb_set(target, path, new, create_missing=True):
    if target is None or path is None or new is None or create_missing is None:
        return None
    path = _coerce_path(path)
    if path is None:
        return None
    return _set_path(copy.deepcopy(target), path, 0, new, create_missing, None)


def jsonb_insert(target, path, new, insert_after=False):
    if target is None or path is None or new is None or insert_after is None:
        return None
    path = _coerce_path(path)
    if path is None:
        return None
    return _set_path(copy.deepcopy(target), path, 0, new, True, insert_after)


def _set_path(node, path, level, new, create, insert_after):
    """Shared worker for jsonb_set (insert_after=None) and jsonb_insert.

    Only the last path step may create anything; earlier steps must
    exist, or this subtree is returned unchanged.
    """
    npath = len(path)
    if isinstance(node, dict):
        if level < npath:
            key = path[level]
            if key in node:
                if level == npath - 1:
                    if insert_after is None:
                        node[key] = new
                    # jsonb_insert never replaces an existing field
                else:
                    node[key] = _set_path(node[key], path, level + 1, new,
                                          create, insert_after)
            elif level == npath - 1 and (create or insert_after is not None):
                node[key] = new
        return node
    if isinstance(node, list):
        n = len(node)
        idx = _parse_index(path[level], level) if level < npath else n
        neg_oob = False
        if idx < 0:
            if -idx > n:
                neg_oob = True
            else:
                idx += n
        elif idx > 0 and idx >= n:
            idx = n
        if level == npath - 1:
            if neg_oob:
                # negative index past the start: new value goes first
                if create or insert_after is not None:
                    node.insert(0, new)
            elif idx < n:
                if insert_after is None:
                    node[idx] = new
                else:
                    node.insert(idx + 1 if insert_after else idx, new)
            elif create or insert_after is not None:
                # positive index past the end: new value goes last
                node.append(new)
        elif level < npath and not neg_oob and idx < n:
            node[idx] = _set_path(node[idx], path, level + 1, new, create,
                                  insert_after)
        return node
    if level == npath:
        # empty path on a scalar replaces it
        return new
    raise JsonbError("cannot set path in scalar")


# ------------------------------ jsonb_strip_nulls ------------------------------

def strip_nulls(a):
    if a is None:
        return None
    return _strip(a)


def _strip(v):
    if isinstance(v, dict):
        # object fields with null values are dropped, recursively
        return {k: _strip(x) for k, x in v.items() if x is not JSON_NULL}
    if isinstance(v, list):
        # array elements are processed but nulls among them are kept
        return [_strip(x) for x in v]
    return v


# ------------------------------ jsonb_array_length / jsonb_typeof ------------------------------

def array_length(a):
    if a is None:
        return None
    if isinstance(a, list):
        return len(a)
    if isinstance(a, dict):
        raise JsonbError("cannot get array length of a non-array")
    raise JsonbError("cannot get array length of a scalar")


def typeof(a):
    if a is None:
        return None
    if isinstance(a, dict):
        return "object"
    if isinstance(a, list):
        return "array"
    if isinstance(a, bool):
        return "boolean"
    if a is JSON_NULL:
        return "null"
    if isinstance(a, Decimal):
        return "number"
    return "string"
