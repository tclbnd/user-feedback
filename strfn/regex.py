"""regexp_replace(source, pattern, replacement [, flags]).

PostgreSQL replaces the first match only, unless flag 'g' requests a global
replacement.  The replacement string uses PostgreSQL escapes: ``\\&`` is the
whole match, ``\\n`` a back reference, ``\\\\`` a literal backslash; any other
back-slash sequence is copied literally.
"""
import re

from .basic import FunctionError


def _make_repl(repl, groups):
    def expand(match):
        out = []
        i = 0
        n = len(repl)
        while i < n:
            ch = repl[i]
            if ch != "\\" or i + 1 >= n:
                out.append(ch)
                i += 1
                continue
            nxt = repl[i + 1]
            if nxt == "\\":
                out.append("\\")
                i += 2
            elif nxt == "&":
                out.append(match.group(0))
                i += 2
            elif nxt.isdigit():
                ref = int(nxt)
                if ref > groups:
                    raise FunctionError("invalid backreference number")
                # An unmatched optional group yields an empty replacement.
                out.append(match.group(ref) or "")
                i += 2
            else:
                out.append("\\" + nxt)
                i += 2
        return "".join(out)

    return expand


def regexp_replace(s, pattern, repl, flags=""):
    if s is None or pattern is None or repl is None:
        return None
    reflags = 0
    global_ = False
    for flag in flags:
        if flag == "i":
            reflags |= re.IGNORECASE
        elif flag == "m":
            reflags |= re.MULTILINE
        elif flag == "g":
            global_ = True
        else:
            raise FunctionError("invalid regexp option: %s" % flag)
    try:
        compiled = re.compile(pattern, reflags)
    except re.error as exc:
        raise FunctionError("invalid regular expression: %s" % exc)
    try:
        return compiled.sub(_make_repl(repl, compiled.groups), s,
                            count=0 if global_ else 1)
    except re.error as exc:
        raise FunctionError("invalid regular expression: %s" % exc)
