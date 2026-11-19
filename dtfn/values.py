"""date / timestamp (without time zone) / interval values.

Dates and timestamps come in as ISO text ('2026-11-20', '2026-11-20 10:30:00');
intervals as (months, days, seconds) triples.  Output follows PostgreSQL's
default DateStyle (ISO) and IntervalStyle (postgres).
"""
from datetime import date, datetime


class DateTimeError(Exception):
    pass


def parse_date(text):
    return date.fromisoformat(text)


def parse_ts(text):
    return datetime.fromisoformat(text)


def show_date(d):
    return d.isoformat()


def show_ts(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")


def show_interval(months, days, seconds):
    parts = []
    years, mons = divmod(months, 12)
    if years:
        parts.append(f"{years} years" if years != 1 else "1 year")
    if mons:
        parts.append(f"{mons} mons" if mons != 1 else "1 mon")
    if days:
        parts.append(f"{days} days" if days != 1 else "1 day")
    if seconds or not parts:
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        parts.append(f"{h:02d}:{m:02d}:{s:02d}")
    return " ".join(parts)
