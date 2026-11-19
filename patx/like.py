"""LIKE / ILIKE and SIMILAR TO (PostgreSQL semantics), translated to Python regexes."""
import re


class PatternError(Exception):
    pass


def like_to_regex(pattern, escape="\\"):
    out = []
    for ch in pattern:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def like(s, pattern, escape="\\", icase=False):
    if s is None or pattern is None:
        return None
    flags = re.DOTALL | (re.IGNORECASE if icase else 0)
    return re.match(like_to_regex(pattern, escape), s, flags) is not None


def similar_to_regex(pattern, escape="\\"):
    out = []
    for ch in pattern:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(ch)
    return "".join(out)


def similar(s, pattern, escape="\\"):
    if s is None or pattern is None:
        return None
    return re.search(similar_to_regex(pattern, escape), s, re.DOTALL) is not None


def substring_similar(s, pattern, escape):
    if s is None or pattern is None:
        return None
    m = re.search(similar_to_regex(pattern.replace(escape + '"', ""), escape), s, re.DOTALL)
    return m.group(0) if m else None
