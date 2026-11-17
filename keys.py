"""ORDER BY parsing and row sort keys."""


class OrderError(ValueError):
    pass


def parse(text):
    keys = []
    for part in text.split(","):
        words = part.split()
        if not words:
            raise OrderError(f"empty sort key in {text!r}")
        key = {"col": words[0], "desc": False, "nulls_first": None, "collate": "binary"}
        rest = [w.lower() for w in words[1:]]
        while rest:
            word = rest.pop(0)
            if word in ("asc", "desc"):
                key["desc"] = word == "desc"
            elif word == "collate" and rest and rest[0] in ("binary", "nocase"):
                key["collate"] = rest.pop(0)
            elif word == "nulls" and rest and rest[0] in ("first", "last"):
                key["nulls_first"] = rest.pop(0) == "first"
            else:
                raise OrderError(f"unexpected {word!r} in sort key {part.strip()!r}")
        keys.append(key)
    return keys


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def row_key(keys):
    def key(row):
        parts = []
        for k in keys:
            v = row.get(k["col"])
            if v is None:
                parts.append((-1 if k["nulls_first"] else 1, 0, 0, ""))
            elif _is_num(v):
                parts.append((0, 0, v, ""))
            else:
                parts.append((0, 1, 0, v))
        return tuple(parts)
    return key
