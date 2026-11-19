import unittest

from dtcall import call


class BasicTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(call(["extract", "year", "2026-11-20"])["value"], 2026)
        self.assertEqual(call(["date_trunc", "month", "2026-11-20 10:00:00"])["value"], "2026-11-01 00:00:00")
        self.assertEqual(call(["date_plus_int", "2026-02-28", 1])["value"], "2026-03-01")


if __name__ == "__main__":
    unittest.main()
