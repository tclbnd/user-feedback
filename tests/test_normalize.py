import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from design import violations  # noqa: E402
from fd import closure, minimal_cover  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_closure(self):
        self.assertEqual(closure(["A"], [(["A"], ["B"]), (["B"], ["C"])]), {"A", "B", "C"})

    def test_cover_splits_right_sides(self):
        cover = minimal_cover([(["A"], ["B", "C"])], ["A", "B", "C"])
        self.assertEqual(cover, [(("A",), ("B",)), (("A",), ("C",))])

    def test_sample_violation(self):
        spec = {"fds": [{"lhs": ["zip"], "rhs": ["city"]}]}
        rows = [{"zip": "1", "city": "X"}, {"zip": "1", "city": "Y"}]
        self.assertEqual(violations(spec, rows)[0]["values"], ["1"])


if __name__ == "__main__":
    unittest.main()
