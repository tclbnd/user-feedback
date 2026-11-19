"""extract, date_trunc, arithmetic, make_date and justify_* (PostgreSQL names)."""
import calendar
from datetime import date, datetime, timedelta

from .values import DateTimeError


def extract(field, d):
    if field == "year":
        return d.year
    if field == "month":
        return d.month
    if field == "day":
        return d.day
    if field == "dow":
        return d.weekday()
    if field == "isodow":
        return d.weekday() + 1
    if field == "doy":
        return d.timetuple().tm_yday
    if field == "week":
        return int(d.strftime("%U"))
    if field == "isoyear":
        return d.year
    if field == "quarter":
        return (d.month - 1) // 3 + 1
    raise DateTimeError(f'unit "{field}" not recognized')


def date_trunc(field, t):
    if field == "day":
        return datetime(t.year, t.month, t.day)
    if field == "week":
        start = t - timedelta(days=(t.weekday() + 1) % 7)
        return datetime(start.year, start.month, start.day)
    if field == "month":
        return datetime(t.year, t.month, 1)
    if field == "quarter":
        return datetime(t.year, 3 * ((t.month - 1) // 3) + 1, 1)
    if field == "year":
        return datetime(t.year, 1, 1)
    if field == "decade":
        return datetime(t.year - t.year % 10, 1, 1)
    if field == "century":
        return datetime(t.year - t.year % 100, 1, 1)
    if field == "millennium":
        return datetime(t.year - t.year % 1000, 1, 1)
    raise DateTimeError(f'unit "{field}" not recognized')


def add_months(t, n):
    return t + timedelta(days=30 * n)


def add_interval(t, months, days, seconds):
    return add_months(t, months) + timedelta(days=days, seconds=seconds)


def date_plus_int(d, n):
    return d + timedelta(days=n)


def date_minus_date(a, b):
    return (a - b).days


def make_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError as exc:
        raise DateTimeError(f"date field value out of range: {y}-{m:02d}-{d:02d}") from exc


def justify_days(months, days, seconds):
    return months + days // 30, days % 30, seconds


def justify_hours(months, days, seconds):
    return months, days + int(seconds // 86400), seconds % 86400


def justify_interval(months, days, seconds):
    m, d, s = justify_hours(months, days, seconds)
    return justify_days(m, d, s)
