import unittest

from winx.engine import evaluate

ROWS = [{"id": i, "g": "a", "k": k, "v": v} for i, (k, v) in enumerate([(1, 10), (2, 20), (3, 30)], 1)]


class BasicTests(unittest.TestCase):
    def test_running_sum_unique_keys(self):
        out = evaluate(ROWS, {"fn": "sum", "arg": "v", "window": {"partition_by": ["g"], "order_by": [{"col": "k"}]}})
        self.assertEqual(out, {1: 10, 2: 30, 3: 60})

    def test_rows_moving_sum(self):
        w = {"order_by": [{"col": "k"}], "frame": {"mode": "rows", "start": {"type": "preceding", "offset": 1},
                                                   "end": {"type": "current_row"}}}
        self.assertEqual(evaluate(ROWS, {"fn": "sum", "arg": "v", "window": w}), {1: 10, 2: 30, 3: 50})


if __name__ == "__main__":
    unittest.main()
