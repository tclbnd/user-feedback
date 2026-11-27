import unittest

from wsakit.wsa import WsaError, parse


def wsa(w, h, frames):
    n = len(frames)
    head = b"".join(v.to_bytes(2, "little") for v in (n, 0, 0, w, h, 256, 0))
    pos = len(head) + 4 * (n + 2)
    offs, body = [], b""
    for f in frames:
        offs.append(pos + len(body))
        body += f
    offs += [pos + len(body), 0]
    return head + b"".join(o.to_bytes(4, "little") for o in offs) + body


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        frame = bytes([0x80 | 8, 4, 1, 2, 3, 4, 0x80, 0x00, 0x00, 0x80])
        out = parse(wsa(2, 2, [frame]))
        self.assertEqual(out["frames"], 1)
        self.assertEqual(out["loop"], False)

    def test_short_header(self):
        with self.assertRaises(WsaError):
            parse(b"\x01\x00")


if __name__ == "__main__":
    unittest.main()
