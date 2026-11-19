import unittest

from subq.logic import and_, compare, distinct_from, or_


class LogicTests(unittest.TestCase):
    def test_three_valued(self):
        self.assertIsNone(compare(None, "=", 1))
        self.assertFalse(and_([True, False, None]))
        self.assertTrue(or_([None, True]))
        self.assertIsNone(or_([None, False]))

    def test_distinct_from(self):
        self.assertFalse(distinct_from(None, None))
        self.assertTrue(distinct_from(None, 1))


if __name__ == "__main__":
    unittest.main()
