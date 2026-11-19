import unittest

from netaddr.functions import FUNCTIONS as F


class FunctionTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(F["host"]("192.168.1.5/24"), "192.168.1.5")
        self.assertEqual(F["masklen"]("192.168.1.5/24"), "24")
        self.assertEqual(F["netmask"]("192.168.1.5/24"), "255.255.255.0")
        self.assertEqual(F["family"]("::1"), "6")


if __name__ == "__main__":
    unittest.main()
