import unittest

from oledkit.ssd1306 import OledError, parse


def log(events):
    return b"SSD1306\0" + len(events).to_bytes(4, "little") + b"".join(bytes(e) for e in events)


class BasicTests(unittest.TestCase):
    def test_display_on(self):
        out = parse(log([(0, 0xAF), (1, 0xFF)]))
        self.assertTrue(out["display_on"])
        self.assertEqual(out["lit"], 8)

    def test_not_a_log(self):
        with self.assertRaises(OledError):
            parse(b"SSD1305\0" + bytes(4))


if __name__ == "__main__":
    unittest.main()
