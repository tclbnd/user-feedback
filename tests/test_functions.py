import unittest

from netaddr.functions import FUNCTIONS as F
from netaddr.types import NetError, output, parse_cidr, parse_inet


def out(kind, text):
    return output(parse_inet(text) if kind == "inet" else parse_cidr(text))


def expect_error(fn, *args):
    try:
        fn(*args)
    except NetError:
        return
    raise AssertionError("NetError not raised")


class ParseAndOutputTests(unittest.TestCase):
    def test_inet_text_hides_full_mask(self):
        self.assertEqual(out("inet", "10.20.0.7/32"), "10.20.0.7")
        self.assertEqual(out("inet", "10.20.0.7/24"), "10.20.0.7/24")
        self.assertEqual(out("inet", "::1"), "::1")
        self.assertEqual(out("inet", "2001:4f8:3:ba::1/64"), "2001:4f8:3:ba::1/64")
        self.assertEqual(out("inet", "ABCD::1"), "abcd::1")
        self.assertEqual(out("inet", "::ffff:1.2.3.4"), "::ffff:1.2.3.4")
        self.assertEqual(out("inet", "1.2.3.4/255.255.255.0"), "1.2.3.4/24")
        self.assertEqual(out("inet", "010.000.000.001"), "10.0.0.1")

    def test_cidr_text_keeps_full_mask(self):
        self.assertEqual(out("cidr", "10.20.0.0/16"), "10.20.0.0/16")
        self.assertEqual(out("cidr", "10.20.0.7/32"), "10.20.0.7/32")
        self.assertEqual(out("cidr", "0.0.0.0/0"), "0.0.0.0/0")
        self.assertEqual(out("cidr", "::/0"), "::/0")

    def test_rejected_inputs(self):
        for bad in ("10.20", "256.1.1.1", "1.2.3", "1.2.3.4.5", "1.2.3.4/33",
                    "1.2.3.4/255.0.255.0", "1.2.3.4/255.255.255", "1.2.3.4/",
                    "1.2.3.4/-1", "", "fe80::1%eth0", "[::1]", "::1/129",
                    "1.2.3.4/abc", "::ffff:1.2.3.4/129", "1..2.3.4"):
            expect_error(parse_inet, bad)
            expect_error(parse_cidr, bad)

    def test_cidr_rejects_host_bits(self):
        expect_error(parse_cidr, "10.20.0.7/24")
        expect_error(parse_cidr, "2001:4f8:3:ba::1/64")
        # zero host bits accepted
        self.assertEqual(out("cidr", "10.20.0.0/24"), "10.20.0.0/24")
        self.assertEqual(out("cidr", "2001:4f8:3:ba::/64"), "2001:4f8:3:ba::/64")


class ScalarFunctionTests(unittest.TestCase):
    def test_host(self):
        self.assertEqual(F["host"]("192.168.1.5/24"), "192.168.1.5")
        self.assertEqual(F["host"]("::ffff:1.2.3.4/120"), "::ffff:1.2.3.4")
        self.assertEqual(F["host"]("2001:4f8:3:BA::2/64"), "2001:4f8:3:ba::2")

    def test_text(self):
        self.assertEqual(F["text"]("192.168.1.5"), "192.168.1.5")
        self.assertEqual(F["text"]("192.168.1.5/24"), "192.168.1.5/24")

    def test_masklen_and_family(self):
        self.assertEqual(F["masklen"]("192.168.1.5/24"), "24")
        self.assertEqual(F["masklen"]("::1/64"), "64")
        self.assertEqual(F["family"]("192.168.1.5/24"), "4")
        self.assertEqual(F["family"]("::1"), "6")

    def test_netmask_hostmask(self):
        self.assertEqual(F["netmask"]("192.168.1.5/24"), "255.255.255.0")
        self.assertEqual(F["hostmask"]("192.168.1.5/24"), "0.0.0.255")
        self.assertEqual(F["netmask"]("::1/64"),
                         "ffff:ffff:ffff:ffff::")
        self.assertEqual(F["hostmask"]("::1/64"), "::ffff:ffff:ffff:ffff")
        self.assertEqual(F["netmask"]("10.0.0.0/0"), "0.0.0.0")
        self.assertEqual(F["hostmask"]("10.0.0.0/0"), "255.255.255.255")

    def test_same_family(self):
        self.assertEqual(F["inet_same_family"]("1.2.3.4", "5.6.7.8/8"), "t")
        self.assertEqual(F["inet_same_family"]("1.2.3.4", "::1"), "f")
        self.assertEqual(F["inet_same_family"]("::1", "::2/64"), "t")


class NetworkFunctionTests(unittest.TestCase):
    def test_network(self):
        self.assertEqual(output(F["network"]("192.168.1.5/24")), "192.168.1.0/24")
        self.assertEqual(output(F["network"]("192.168.1.5/32")), "192.168.1.5")
        self.assertEqual(output(F["network"]("2001:4f8:3:ba::1/64")),
                         "2001:4f8:3:ba::/64")
        # cidr argument is cast to inet, so /32 renders without a mask
        self.assertEqual(output(F["network"](parse_cidr("10.0.0.0/8"))),
                         "10.0.0.0/8")

    def test_broadcast_keeps_mask(self):
        self.assertEqual(output(F["broadcast"]("192.168.1.5/24")),
                         "192.168.1.255/24")
        self.assertEqual(output(F["broadcast"]("192.168.1.5/30")),
                         "192.168.1.7/30")
        self.assertEqual(output(F["broadcast"]("2001:4f8:3:ba::1/64")),
                         "2001:4f8:3:ba:ffff:ffff:ffff:ffff/64")
        self.assertEqual(output(F["broadcast"]("10.0.0.0/32")), "10.0.0.0")

    def test_abbrev(self):
        self.assertEqual(F["abbrev"](parse_cidr("10.20.0.0/16")), "10.20/16")
        self.assertEqual(F["abbrev"](parse_cidr("10.0.0.0/8")), "10/8")
        self.assertEqual(F["abbrev"](parse_cidr("10.1.0.0/16")), "10.1/16")
        self.assertEqual(F["abbrev"](parse_cidr("192.168.0.0/24")),
                         "192.168.0/24")
        self.assertEqual(F["abbrev"](parse_cidr("0.0.0.0/0")), "0/0")
        self.assertEqual(F["abbrev"](parse_cidr("10.20.0.0/32")),
                         "10.20.0.0/32")
        # inet keeps all octets; v6 only gets the usual compression
        self.assertEqual(F["abbrev"]("10.1.0.0/16"), "10.1.0.0/16")
        self.assertEqual(F["abbrev"](parse_cidr("2001:4f8:3:ba::/64")),
                         "2001:4f8:3:ba::/64")

    def test_set_masklen(self):
        self.assertEqual(output(F["set_masklen"]("192.168.1.5/24", 16)),
                         "192.168.1.5/16")
        self.assertEqual(output(F["set_masklen"]("::1/64", 96)), "::1/96")
        # cidr variant zeroes host bits and stays cidr
        self.assertEqual(output(F["set_masklen"](parse_cidr("10.0.0.0/8"), 24)),
                         "10.0.0.0/24")
        expect_error(F["set_masklen"], "1.2.3.4", 33)
        expect_error(F["set_masklen"], "::1", 129)
        expect_error(F["set_masklen"], "1.2.3.4", -1)

    def test_inet_merge(self):
        self.assertEqual(output(F["inet_merge"]("192.168.1.5", "192.168.1.10")),
                         "192.168.1.0/28")
        self.assertEqual(output(F["inet_merge"]("10.0.0.255", "10.0.1.1")),
                         "10.0.0.0/23")
        self.assertEqual(output(F["inet_merge"]("192.168.1.5", "192.168.1.5")),
                         "192.168.1.5")
        self.assertEqual(output(F["inet_merge"]("::1", "::2")), "::/126")
        expect_error(F["inet_merge"], "1.2.3.4", "::1")


class ContainmentTests(unittest.TestCase):
    def test_strict_contained(self):
        self.assertEqual(F["<<"]("10.20.0.7", "10.20.0.0/24"), "t")
        self.assertEqual(F["<<"]("10.20.0.0/24", "10.20.0.0/24"), "f")
        self.assertEqual(F["<<"]("10.20.0.0/25", "10.20.0.0/24"), "t")
        self.assertEqual(F["<<"]("10.20.1.0/24", "10.20.0.0/24"), "f")
        self.assertEqual(F["<<"]("::1", "::/0"), "t")
        self.assertEqual(F["<<"]("::/0", "::/0"), "f")
        # different families are just false, no error
        self.assertEqual(F["<<"]("1.2.3.4", "::/0"), "f")

    def test_contained_eq(self):
        self.assertEqual(F["<<="]("10.20.0.0/24", "10.20.0.0/24"), "t")
        self.assertEqual(F["<<="]("10.20.1.0/24", "10.20.0.0/24"), "f")
        self.assertEqual(F["<<="]("::/0", "::/0"), "t")
        self.assertEqual(F["<<="]("1.2.3.4", "::/0"), "f")

    def test_strict_contains(self):
        self.assertEqual(F[">>"]("10.20.0.0/24", "10.20.0.7"), "t")
        self.assertEqual(F[">>"]("10.20.0.0/24", "10.20.0.0/24"), "f")
        self.assertEqual(F[">>"]("10.20.0.0/16", "10.20.0.0/24"), "t")

    def test_overlaps(self):
        self.assertEqual(F["&&"]("10.20.0.0/24", "10.20.0.128/25"), "t")
        self.assertEqual(F["&&"]("10.20.0.0/25", "10.20.0.128/25"), "f")
        self.assertEqual(F["&&"]("::/0", "::1"), "t")
        self.assertEqual(F["&&"]("10.0.0.0/8", "::/0"), "f")


class ArithmeticTests(unittest.TestCase):
    def test_plus_keeps_mask_and_type(self):
        self.assertEqual(output(F["+"]("192.168.1.6/24", 25)),
                         "192.168.1.31/24")
        self.assertEqual(output(F["+"]("192.168.1.6", 25)), "192.168.1.31")
        self.assertEqual(output(F["+"]("::1/64", 15)), "::10/64")
        self.assertEqual(output(F["+"]("255.255.255.255", -1)),
                         "255.255.255.254")
        expect_error(F["+"], "255.255.255.255", 1)
        expect_error(F["+"], "0.0.0.0", -1)
        expect_error(F["+"], "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", 1)

    def test_minus_integer_keeps_mask(self):
        self.assertEqual(output(F["-"]("192.168.1.31/24", 25)),
                         "192.168.1.6/24")
        expect_error(F["-"], "0.0.0.0", 1)

    def test_minus_addresses_returns_integer(self):
        self.assertEqual(F["-"]("192.168.1.10", "192.168.1.5"), 5)
        self.assertEqual(F["-"]("::10", "::1"), 15)
        self.assertEqual(F["-"]("::1", "::10"), -15)
        expect_error(F["-"], "1.2.3.4", "::1")
        big1 = "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
        expect_error(F["-"], big1, "::")

    def test_bit_not(self):
        self.assertEqual(output(F["~"]("192.168.1.5/24")),
                         "63.87.254.250/24")
        self.assertEqual(output(F["~"]("255.255.255.255")), "0.0.0.0")
        self.assertEqual(output(F["~"]("::1/64")),
                         "ffff:ffff:ffff:ffff:ffff:ffff:ffff:fffe/64")

    def test_bit_and(self):
        self.assertEqual(output(F["&"]("192.168.1.5", "0.0.0.255")),
                         "0.0.0.5")
        self.assertEqual(output(F["&"]("::ffff/64", "::1/64")), "::1/64")
        expect_error(F["&"], "1.2.3.4", "::1")


if __name__ == "__main__":
    unittest.main()
