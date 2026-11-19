"""to_char(date/timestamp, format) — the template patterns the reports use.

Supported: YYYY, MM, DD, HH24, HH12 (HH), MI, SS, AM/PM (A.M./P.M.),
Mon, Month, Day, Dy, D, DDD, IW, Q, the FM prefix and double-quoted
literals.  Pattern matching is case-insensitive; for the textual patterns
the case of the pattern chooses the case of the output (DAY -> MONDAY,
day -> monday, Day -> Monday).  Without FM, Day/Month are blank-padded
to 9 characters and numeric patterns are zero-padded.
"""

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
         "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

# numeric patterns: pattern -> (zero-pad width, value function)
_NUMERIC = {
    "YYYY": (4, lambda t: t.year),
    "MM": (2, lambda t: t.month),
    "DD": (2, lambda t: t.day),
    "DDD": (3, lambda t: t.timetuple().tm_yday),
    # day of week, Sunday = 1 .. Saturday = 7
    "D": (1, lambda t: t.isoweekday() % 7 + 1),
    "HH24": (2, lambda t: t.hour),
    "HH12": (2, lambda t: t.hour % 12 or 12),
    "HH": (2, lambda t: t.hour % 12 or 12),
    "MI": (2, lambda t: t.minute),
    "SS": (2, lambda t: t.second),
    # ISO 8601 week number
    "IW": (2, lambda t: t.isocalendar()[1]),
    "Q": (1, lambda t: (t.month - 1) // 3 + 1),
}

# all patterns, longest first so e.g. DDD wins over DD wins over D
_PATTERNS = (
    ["MONTH"] +
    ["YYYY", "HH24", "HH12", "A.M.", "P.M."] +
    ["DDD", "DAY", "MON"] +
    ["HH", "MM", "DD", "MI", "SS", "DY", "IW", "AM", "PM"] +
    ["D", "Q"]
)


def to_char(t, fmt):
    out, i = [], 0
    while i < len(fmt):
        fm = False
        if fmt[i:i + 2].upper() == "FM":
            fm, i = True, i + 2
        if i < len(fmt) and fmt[i] == '"':
            j = fmt.find('"', i + 1)
            if j == -1:
                j = len(fmt)
            out.append(fmt[i + 1:j])
            i = j + 1 if j < len(fmt) else len(fmt)
            continue
        for p in _PATTERNS:
            if fmt[i:i + len(p)].upper() == p:
                out.append(_field(t, p, fmt[i:i + len(p)], fm))
                i += len(p)
                break
        else:
            if i < len(fmt):
                out.append(fmt[i])
            i += 1
    return "".join(out)


def _cased(name, text):
    if text.isupper():
        return name.upper()
    if text.islower():
        return name.lower()
    return name  # initcap, as stored


def _field(t, p, text, fm):
    if p in _NUMERIC:
        width, func = _NUMERIC[p]
        v = func(t)
        return str(v) if fm else f"{v:0{width}d}"
    if p == "MONTH":
        name = _cased(_MONTHS[t.month - 1], text)
        return name if fm else name.ljust(9)
    if p == "MON":
        return _cased(_MONTHS[t.month - 1][:3], text)
    if p == "DAY":
        name = _cased(_DAYS[t.weekday()], text)
        return name if fm else name.ljust(9)
    if p == "DY":
        return _cased(_DAYS[t.weekday()][:3], text)
    if p in ("AM", "PM"):
        s = "AM" if t.hour < 12 else "PM"
        return _cased(s, text)
    if p in ("A.M.", "P.M."):
        s = "A.M." if t.hour < 12 else "P.M."
        if text.isupper():
            return s
        if text.islower():
            return s.lower()
        return s
    return text
