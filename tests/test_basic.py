import unittest

from sidkit.sid import parse

from tunes import tune


class BasicTests(unittest.TestCase):
    def test_name(self):
        out = parse(tune())
        self.assertEqual((out["format"], out["name"], out["data_bytes"]), ("PSID", "Test", 16))

    def test_empty_strings(self):
        out = parse(tune())
        self.assertEqual((out["author"], out["released"]), (None, None))
