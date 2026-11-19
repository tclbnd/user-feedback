"""to_char(date/timestamp, format) — the template patterns the reports use.

Supported: YYYY, MM, DD, HH24, HH12, MI, SS, AM/PM, Mon, Month, Day, Dy,
D, DDD, IW, Q, and the FM prefix.
"""


def to_char(t, fmt):
    out, i = [], 0
    pats = ["YYYY", "Month", "Mon", "Day", "Dy", "DDD", "DD", "D", "HH24", "HH12", "MI", "SS", "AM", "PM",
            "MM", "IW", "Q"]
    while i < len(fmt):
        fm = False
        if fmt.startswith("FM", i):
            fm, i = True, i + 2
        for p in pats:
            if fmt.startswith(p, i):
                out.append(_field(t, p, fm))
                i += len(p)
                break
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out)


def _field(t, p, fm):
    if p == "YYYY":
        return f"{t.year:04d}"
    if p == "MM":
        return f"{t.month:02d}"
    if p == "DD":
        return f"{t.day:02d}"
    if p == "HH24":
        return f"{t.hour:02d}"
    if p == "HH12":
        return f"{t.hour % 12:02d}"
    if p == "MI":
        return f"{t.minute:02d}"
    if p == "SS":
        return f"{t.second:02d}"
    if p in ("AM", "PM"):
        return "AM" if t.hour < 12 else "PM"
    if p == "Mon":
        return t.strftime("%b")
    if p == "Month":
        return t.strftime("%B")
    if p == "Day":
        return t.strftime("%A")
    if p == "Dy":
        return t.strftime("%a")
    if p == "D":
        return str(t.weekday() + 1)
    if p == "DDD":
        return str(t.timetuple().tm_yday)
    if p == "IW":
        return str(t.isocalendar()[1])
    if p == "Q":
        return str((t.month - 1) // 3 + 1)
    return p
