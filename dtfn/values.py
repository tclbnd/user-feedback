"""date / timestamp (without time zone) / interval values.

Dates and timestamps come in as ISO text ('2026-11-20', '2026-11-20 10:30:00');
intervals as (months, days, seconds) triples.  Output follows PostgreSQL's
default DateStyle (ISO) and IntervalStyle (postgres).
"""
from datetime import date, datetime


class DateTimeError(Exception):
    pass


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DateTimeError(f'invalid input syntax for type date: "{text}"') from exc


def parse_ts(text):
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise DateTimeError(f'invalid input syntax for type timestamp: "{text}"') from exc


def show_date(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _frac(microseconds):
    # fractional seconds with trailing zeros trimmed ('.5', '.25', '.123456')
    return f"{microseconds:06d}".rstrip("0")


def show_ts(t):
    out = (f"{t.year:04d}-{t.month:02d}-{t.day:02d} "
           f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}")
    if t.microsecond:
        out += "." + _frac(t.microsecond)
    return out


def show_interval(months, days, seconds):
    parts = []
    # C-style truncation toward zero, as in PostgreSQL's EncodeInterval
    if months >= 0:
        years = months // 12
    else:
        years = -((-months) // 12)
    mons = months - years * 12
    if years:
        parts.append(f"{years} year" + ("" if years == 1 else "s"))
    if mons:
        parts.append(f"{mons} mon" + ("" if mons == 1 else "s"))
    if days:
        parts.append(f"{days} day" + ("" if days == 1 else "s"))
    micros = int(round(seconds * 1000000))
    if micros or not parts:
        sign = "-" if micros < 0 else ""
        micros = abs(micros)
        h, rem = divmod(micros, 3600000000)
        m, rem = divmod(rem, 60000000)
        s, frac = divmod(rem, 1000000)
        t = f"{sign}{h:02d}:{m:02d}:{s:02d}"
        if frac:
            t += "." + _frac(frac)
        parts.append(t)
    return " ".join(parts)