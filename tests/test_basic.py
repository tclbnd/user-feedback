import unittest

from amrfile import mailbox
from amrfile.summary import summarise


class BasicTests(unittest.TestCase):
    def test_nb_122(self):
        frame = bytes([0x3C]) + bytes(31)
        out = summarise(b"#!AMR\n" + frame * 50)
        self.assertEqual((out["frames"], out["duration_ms"], out["modes"]), (50, 1000, {"12.2": 50}))

    def test_quota(self):
        self.assertEqual(mailbox.usage([{"duration_ms": 1000000}, {"duration_ms": 900000}])["over_quota"], True)


if __name__ == "__main__":
    unittest.main()
