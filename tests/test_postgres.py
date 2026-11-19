import unittest

from dtcall import call


def value(c):
    r = call(c)
    assert "error" not in r, r
    return r["value"]


def error(c):
    r = call(c)
    assert "error" in r, r
    return r["error"]


class ExtractTests(unittest.TestCase):
    def test_dow(self):
        self.assertEqual(value(["extract", "dow", "2026-11-22"]), 0)  # Sunday
        self.assertEqual(value(["extract", "dow", "2026-11-21"]), 6)  # Saturday
        self.assertEqual(value(["extract", "dow", "2026-11-20"]), 5)  # Friday

    def test_isodow(self):
        self.assertEqual(value(["extract", "isodow", "2026-11-22"]), 7)
        self.assertEqual(value(["extract", "isodow", "2026-11-23"]), 1)

    def test_week_isoyear(self):
        self.assertEqual(value(["extract", "week", "2017-01-01"]), 52)
        self.assertEqual(value(["extract", "isoyear", "2017-01-01"]), 2016)
        self.assertEqual(value(["extract", "week", "2020-12-31"]), 53)
        self.assertEqual(value(["extract", "isoyear", "2020-12-31"]), 2020)
        self.assertEqual(value(["extract", "week", "2021-01-04"]), 1)
        self.assertEqual(value(["extract", "isoyear", "2021-01-04"]), 2021)

    def test_doy_quarter(self):
        self.assertEqual(value(["extract", "doy", "2026-12-31"]), 365)
        self.assertEqual(value(["extract", "doy", "2024-12-31"]), 366)
        self.assertEqual(value(["extract", "doy", "2026-01-01"]), 1)
        self.assertEqual(value(["extract", "quarter", "2026-11-20"]), 4)
        self.assertEqual(value(["extract", "quarter", "2026-01-01"]), 1)

    def test_case_insensitive_and_unknown(self):
        self.assertEqual(value(["extract", "DOW", "2026-11-22"]), 0)
        self.assertEqual(
            error(["extract", "foo", "2026-11-22"]),
            'unit "foo" not recognized for type timestamp without time zone')


class DateTruncTests(unittest.TestCase):
    def test_week_starts_monday(self):
        self.assertEqual(value(["date_trunc", "week", "2026-11-22 10:00:00"]),
                         "2026-11-16 00:00:00")
        self.assertEqual(value(["date_trunc", "week", "2026-11-16 00:00:00"]),
                         "2026-11-16 00:00:00")

    def test_quarter(self):
        self.assertEqual(value(["date_trunc", "quarter", "2026-11-20 10:00:00"]),
                         "2026-10-01 00:00:00")

    def test_decade_century_millennium(self):
        self.assertEqual(value(["date_trunc", "decade", "2026-11-20 10:00:00"]),
                         "2020-01-01 00:00:00")
        self.assertEqual(value(["date_trunc", "century", "2000-12-31 10:00:00"]),
                         "1901-01-01 00:00:00")
        self.assertEqual(value(["date_trunc", "century", "2001-01-01 10:00:00"]),
                         "2001-01-01 00:00:00")
        self.assertEqual(value(["date_trunc", "millennium", "2000-06-15 10:00:00"]),
                         "1001-01-01 00:00:00")
        self.assertEqual(value(["date_trunc", "millennium", "2001-06-15 10:00:00"]),
                         "2001-01-01 00:00:00")

    def test_unknown_unit(self):
        self.assertEqual(
            error(["date_trunc", "foo", "2026-11-20 10:00:00"]),
            'unit "foo" not recognized for type timestamp without time zone')


class ArithmeticTests(unittest.TestCase):
    def test_month_clamping(self):
        self.assertEqual(value(["ts_plus_interval", "2026-01-31 00:00:00", [1, 0, 0]]),
                         "2026-02-28 00:00:00")
        self.assertEqual(value(["ts_plus_interval", "2024-01-31 00:00:00", [1, 0, 0]]),
                         "2024-02-29 00:00:00")
        self.assertEqual(value(["ts_plus_interval", "2026-03-31 00:00:00", [-1, 0, 0]]),
                         "2026-02-28 00:00:00")

    def test_months_then_days_then_seconds(self):
        self.assertEqual(value(["ts_plus_interval", "2026-01-31 00:00:00", [1, 1, 0]]),
                         "2026-03-01 00:00:00")
        self.assertEqual(value(["ts_plus_interval", "2026-01-01 00:00:00", [0, 1, 3600]]),
                         "2026-01-02 01:00:00")

    def test_fractional_seconds(self):
        self.assertEqual(value(["ts_plus_interval", "2026-01-01 00:00:00", [0, 0, 1.5]]),
                         "2026-01-01 00:00:01.5")

    def test_date_plus_int(self):
        self.assertEqual(value(["date_plus_int", "2026-02-28", 1]), "2026-03-01")
        self.assertEqual(value(["date_plus_int", "2026-03-01", -1]), "2026-02-28")
        self.assertEqual(error(["date_plus_int", "9999-12-31", 1]), "date out of range")

    def test_date_minus_date(self):
        self.assertEqual(value(["date_minus_date", "2026-03-01", "2026-02-01"]), 28)

    def test_make_date(self):
        self.assertEqual(value(["make_date", 2026, 2, 28]), "2026-02-28")
        self.assertEqual(error(["make_date", 2026, 2, 30]),
                         "date field value out of range: 2026-02-30")
        self.assertEqual(error(["make_date", 2026, 13, 1]),
                         "date field value out of range: 2026-13-01")


class JustifyTests(unittest.TestCase):
    def test_justify_days(self):
        self.assertEqual(value(["justify_days", [0, 35, 0]]), "1 mon 5 days")
        self.assertEqual(value(["justify_days", [0, 30, 0]]), "1 mon")
        self.assertEqual(value(["justify_days", [0, -35, 0]]), "-1 mons -5 days")

    def test_justify_hours(self):
        self.assertEqual(value(["justify_hours", [0, 0, 90000]]), "1 day 01:00:00")
        self.assertEqual(value(["justify_hours", [0, 0, -90000]]), "-1 days -01:00:00")

    def test_justify_interval(self):
        self.assertEqual(value(["justify_interval", [1, 30, 86400]]), "2 mons 1 day")
        self.assertEqual(value(["justify_interval", [0, -30, -86400]]),
                         "-1 mons -1 days")


class IntervalOutTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(value(["interval_out", [0, 0, 0]]), "00:00:00")
        self.assertEqual(value(["interval_out", [14, 3, 3661]]),
                         "1 year 2 mons 3 days 01:01:01")
        self.assertEqual(value(["interval_out", [1, 0, 0]]), "1 mon")
        self.assertEqual(value(["interval_out", [12, 0, 0]]), "1 year")

    def test_negative(self):
        self.assertEqual(value(["interval_out", [-14, 0, 0]]), "-1 years -2 mons")
        self.assertEqual(value(["interval_out", [0, -1, 7200]]), "-1 days 02:00:00")
        self.assertEqual(value(["interval_out", [0, 0, -3600]]), "-01:00:00")
        self.assertEqual(value(["interval_out", [-1, -1, -3600]]),
                         "-1 mons -1 days -01:00:00")

    def test_hours_not_normalized_and_fraction(self):
        self.assertEqual(value(["interval_out", [0, 0, 90000]]), "25:00:00")
        self.assertEqual(value(["interval_out", [0, 0, 1.5]]), "00:00:01.5")
        self.assertEqual(value(["interval_out", [0, 0, -0.5]]), "-00:00:00.5")


class ToCharTests(unittest.TestCase):
    def test_names_and_padding(self):
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "Day"]), "Friday   ")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "FMDay"]), "Friday")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "Month"]), "November ")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "FMMonth"]), "November")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "Mon"]), "Nov")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "Dy"]), "Fri")

    def test_name_case(self):
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "DAY"]), "FRIDAY   ")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "day"]), "friday   ")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "MONTH"]), "NOVEMBER ")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "month"]), "november ")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "yyyy-mm-dd"]),
                         "2026-11-20")

    def test_d_ddd_iw(self):
        self.assertEqual(value(["to_char", "2026-11-22 00:00:00", "D"]), "1")  # Sunday
        self.assertEqual(value(["to_char", "2026-11-21 00:00:00", "D"]), "7")  # Saturday
        self.assertEqual(value(["to_char", "2026-01-05 00:00:00", "DDD"]), "005")
        self.assertEqual(value(["to_char", "2026-01-05 00:00:00", "FMDDD"]), "5")
        self.assertEqual(value(["to_char", "2017-01-01 00:00:00", "IW"]), "52")
        self.assertEqual(value(["to_char", "2026-11-20 00:00:00", "IW"]), "47")

    def test_hh12_am(self):
        self.assertEqual(value(["to_char", "2026-11-20 00:30:00", "HH12:MI AM"]), "12:30 AM")
        self.assertEqual(value(["to_char", "2026-11-20 12:30:00", "HH12:MI AM"]), "12:30 PM")
        self.assertEqual(value(["to_char", "2026-11-20 13:05:00", "HH12:MI AM"]), "01:05 PM")
        self.assertEqual(value(["to_char", "2026-11-20 13:05:00", "HH24:MI"]), "13:05")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "am"]), "am")
        self.assertEqual(value(["to_char", "2026-11-20 15:05:00", "pm"]), "pm")

    def test_fm_numeric(self):
        self.assertEqual(value(["to_char", "2026-01-05 09:05:00", "FMHH12"]), "9")
        self.assertEqual(value(["to_char", "2026-11-20 00:30:00", "FMDay HH12:MI AM"]),
                         "Friday 12:30 AM")

    def test_misc(self):
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "YYYY-MM-DD Mon"]),
                         "2026-11-20 Nov")
        self.assertEqual(value(["to_char", "2026-11-20 09:05:00", "Q"]), "4")


if __name__ == "__main__":
    unittest.main()
