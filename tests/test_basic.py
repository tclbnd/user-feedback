import unittest

from seqdb.catalog import Catalog, order_number
from seqdb.session import Session


class BasicTests(unittest.TestCase):
    def test_fresh_sequence_starts_at_start(self):
        cat = Catalog()
        cat.create("s", start=5)
        a = Session("a", cat)
        self.assertEqual([a.nextval("s"), a.nextval("s")], [5, 6])

    def test_order_number(self):
        cat = Catalog()
        cat.create("order_seq", start=123)
        self.assertEqual(order_number(Session("w", cat)), "ORD-0000123")


if __name__ == "__main__":
    unittest.main()
