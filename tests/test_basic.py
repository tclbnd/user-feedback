import unittest

from hcakit.hca import parse

from files import hca


class BasicTests(unittest.TestCase):
    def test_layout(self):
        out = parse(hca(blocks=300, bsize=0x200))
        self.assertEqual((out["version"], out["channels"], out["blocks"], out["block_size"]), ("2.0", 2, 300, 512))

    def test_comment(self):
        out = parse(hca(comment="vo_001", cipher=0))
        self.assertEqual((out["comment"], out["cipher"], out["loop"]), ("vo_001", "none", None))
