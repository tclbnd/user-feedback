import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from window import run  # noqa: E402

ROWS = [{"id": 1, "g": "a", "k": 1, "v": 10}, {"id": 2, "g": "a", "k": 2, "v": 20},
        {"id": 3, "g": "a", "k": 3, "v": 30}, {"id": 4, "g": "b", "k": 1, "v": 5}]


class WindowTests(unittest.TestCase):
    def test_row_number_per_partition(self):
        out, _ = run(ROWS, {"partition_by": ["g"], "order_by": [{"col": "k", "desc": True}],
                            "functions": [{"as": "rn", "fn": "row_number"}]})
        self.assertEqual([r["rn"] for r in out], [3, 2, 1, 1])

    def test_rows_moving_sum(self):
        frame = {"mode": "rows", "start": ["preceding", 1], "end": ["current"]}
        out, _ = run(ROWS, {"partition_by": ["g"], "order_by": [{"col": "k"}],
                            "functions": [{"as": "s", "fn": "sum", "arg": "v", "frame": frame}]})
        self.assertEqual([r["s"] for r in out], [10, 30, 50, 5])

    def test_lag_default(self):
        out, _ = run(ROWS, {"partition_by": ["g"], "order_by": [{"col": "k"}],
                            "functions": [{"as": "p", "fn": "lag", "arg": "v", "default": 0}]})
        self.assertEqual([r["p"] for r in out], [0, 10, 20, 0])


if __name__ == "__main__":
    unittest.main()
