"""POSIX regular expression operators and functions (PostgreSQL ARE flavour):
~, ~*, !~, regexp_match, regexp_split_to_array.
"""
import re


def _compile(pattern, icase=False):
    return re.compile(pattern, re.IGNORECASE if icase else 0)


def match_op(s, pattern, icase=False, negate=False):
    if s is None or pattern is None:
        return None
    found = _compile(pattern, icase).match(s) is not None
    return not found if negate else found


def regexp_match(s, pattern, flags=""):
    if s is None or pattern is None:
        return None
    m = _compile(pattern, "i" in flags).search(s)
    if not m:
        return None
    return list(m.groups()) if m.groups() else [m.group(0)]


def regexp_split_to_array(s, pattern, flags=""):
    if s is None or pattern is None:
        return None
    return re.split(pattern, s, flags=re.IGNORECASE if "i" in flags else 0)
