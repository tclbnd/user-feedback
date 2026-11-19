"""extract, date_trunc, arithmetic, make_date and justify_* (PostgreSQL names)."""
import calendar
from datetime import date, datetime, timedelta

from .values import DateTimeError


def _trunc_div(a, b):
    # C-style integer division: quotient truncated toward zero
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def extract(field, d):
    f = field.lower()
    if f == "year":
        return d.year
    if f == "month":
        return d.month
    if f == "day":
        return d.day
    if f == "dow":
        # 0 = Sunday .. 6 = Saturday
        return d.isoweekday() % 7
    if f == "isodow":
        # 1 = Monday .. 7 = Sunday
        return d.isoweekday()
    if f == "doy":
        return d.timetuple().tm_yday
    if f == "week":
        # ISO 8601 week number (weeks start on Monday)
        return d.isocalendar()[1]
    if f == "isoyear":
        return d.isocalendar()[0]
    if f == "quarter":
        return (d.month - 1) // 3 + 1
    if f == "decade":
        return d.year // 10
    if f == "century":
        return (d.year - 1) // 100 + 1
    if f == "millennium":
        return (d.year - 1) // 1000 + 1
    raise DateTimeError(
        f'unit "{f}" not recognized for type timestamp without time zone')


def date_trunc(field, t):
    f = field.lower()
    if f == "microseconds":
        return t
    if f == "milliseconds":
        return t.replace(microsecond=(t.microsecond // 1000) * 1000)
    if f == "second":
        return t.replace(microsecond=0)
    if f == "minute":
        return t.replace(second=0, microsecond=0)
    if f == "hour":
        return t.replace(minute=0, second=0, microsecond=0)
    if f == "day":
        return t.replace(hour=0, minute=0, second=0, microsecond=0)
    if f == "week":
        # ISO week starts on Monday
        start = t - timedelta(days=t.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if f == "month":
        return t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if f == "quarter":
        return datetime(t.year, 3 * ((t.month - 1) // 3) + 1, 1)
    if f == "year":
        return datetime(t.year, 1, 1)
    if f == "decade":
        return datetime((t.year // 10) * 10, 1, 1)
    if f == "century":
        # the first century is years 1-100, so 2000 belongs to the 20th
        return datetime(((t.year - 1) // 100) * 100 + 1, 1, 1)
    if f == "millennium":
        return datetime(((t.year - 1) // 1000) * 1000 + 1, 1, 1)
    raise DateTimeError(
        f'unit "{f}" not recognized for type timestamp without time zone')


def add_months(t, n):
    # months first; the day is clamped to the last day of the target month
    total = t.year * 12 + (t.month - 1) + n
    y, m = divmod(total, 12)
    m += 1
    if not 1 <= y <= 9999:
        raise DateTimeError("timestamp out of range")
    d = min(t.day, calendar.monthrange(y, m)[1])
    return t.replace(year=y, month=m, day=d)


def add_interval(t, months, days, seconds):
    t = add_months(t, months)
    try:
        return t + timedelta(days=days, seconds=seconds)
    except OverflowError as exc:
        raise DateTimeError("timestamp out of range") from exc


def date_plus_int(d, n):
    try:
        return d + timedelta(days=n)
    except OverflowError as exc:
        raise DateTimeError("date out of range") from exc


def date_minus_date(a, b):
    return (a - b).days


def make_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError as exc:
        raise DateTimeError(f"date field value out of range: {y}-{m:02d}-{d:02d}") from exc


def justify_days(months, days, seconds):
    # 30-day months, remainder keeps its sign
    q = _trunc_div(days, 30)
    return months + q, days - q * 30, seconds


def justify_hours(months, days, seconds):
    # 24-hour days, remainder keeps its sign
    micros = int(round(seconds * 1000000))
    q = _trunc_div(micros, 86400000000)
    micros -= q * 86400000000
    s = micros / 1000000
    if s == int(s):
        s = int(s)
    return months, days + q, s


def justify_interval(months, days, seconds):
    months, days, seconds = justify_days(months, days, seconds)
    return justify_hours(months, days, seconds)
