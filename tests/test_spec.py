import struct
import unittest

from s98kit.s98 import S98Error, parse

V3_HEADER = 0x20
ENTRY = 16


def v3(devices, body, timers=(0, 0, 0), tag_off=0, loop_off=0, dump_off=None):
    count = len(devices)
    dump = V3_HEADER + ENTRY * count if dump_off is None else dump_off
    head = b"S983" + struct.pack("<6I", *(timers + (tag_off, dump, loop_off))) + struct.pack("<I", count)
    for t, clock, pan in devices:
        head += struct.pack("<4I", t, clock, pan, 0)
    return head + body


class TimerTests(unittest.TestCase):
    def test_timer_defaults(self):
        out = parse(v3([(4, 7987200, 0)], b"\xff\xfd"))
        self.assertEqual(out["tick"], "1/100")          # 10 / 1000 seconds
        self.assertEqual(out["syncs"], 1)
        self.assertEqual(out["duration"], "1/100")

    def test_timer_explicit(self):
        out = parse(v3([(4, 1000000, 0)], b"\xfd", timers=(441, 44100, 0)))
        self.assertEqual(out["tick"], "1/100")

    def test_v1_fixed_tick(self):
        head = b"S981" + b"\x00" * 12 + struct.pack("<3I", 0, 0x20, 0) + b"\x00" * 4
        out = parse(head + b"\xff\xfd")
        self.assertEqual(out["tick"], "1/250")          # 4 ms per sync
        self.assertEqual(out["duration"], "1/250")

    def test_compat_divisor(self):
        with_compat = parse(v3([(4, 1, 0)], b"\xfd", timers=(10, 1000, 1)))
        plain = parse(v3([(4, 1, 0)], b"\xfd", timers=(10, 1000, 0)))
        self.assertEqual(with_compat["tick"], "1/250")
        self.assertEqual(plain["tick"], "1/100")

    def test_v2_header(self):
        head = b"S982" + struct.pack("<6I", 10, 1000, 0, 0, 0x20, 0) + b"\x00" * 4
        out = parse(head + b"\xff\xff\xfd")
        self.assertEqual(out["version"], "2")
        self.assertEqual(out["syncs"], 2)
        self.assertEqual(out["tick"], "1/100")


class SyncTests(unittest.TestCase):
    def test_ff_is_one_sync(self):
        self.assertEqual(parse(v3([(4, 1, 0)], b"\xff\xff\xff\xfd"))["syncs"], 3)

    def test_fe_single_byte_plus_two(self):
        # value 0 -> 2 syncs; value 5 -> 7 syncs
        self.assertEqual(parse(v3([(4, 1, 0)], b"\xfe\x00\xfd"))["syncs"], 2)
        self.assertEqual(parse(v3([(4, 1, 0)], b"\xfe\x05\xfd"))["syncs"], 7)

    def test_fe_multibyte_lsb_first(self):
        # groups 0x06 then 0x27, least significant first: 0x06 | (0x27 << 7) = 4998, +2 = 5000
        self.assertEqual(parse(v3([(4, 1, 0)], b"\xfe\x86\x27\xfd"))["syncs"], 5000)


class DeviceTests(unittest.TestCase):
    def test_default_devices_without_table(self):
        head = b"S981" + b"\x00" * 12 + struct.pack("<3I", 0, 0x20, 0) + b"\x00" * 4
        out = parse(head + b"\xfd")
        self.assertEqual([d["type"] for d in out["devices"]], ["ym2608", "ym2203", "ym2612"])
        self.assertEqual([d["clock"] for d in out["devices"]], [7987200, 3993600, 7670454])
        self.assertEqual(out["writes"], [0, 0, 0])

    def test_v3_zero_count_uses_defaults(self):
        out = parse(v3([], b"\xfd"))
        self.assertEqual([d["type"] for d in out["devices"]], ["ym2608", "ym2203", "ym2612"])

    def test_unknown_device_type(self):
        out = parse(v3([(14, 123, 7)], b"\xfd"))
        self.assertEqual(out["devices"][0], {"type": "type_14", "clock": 123, "pan": 7})


class WriteTests(unittest.TestCase):
    def test_device_index_bits_one_to_six(self):
        # device 0, both ports: 0x00 (port 0) and 0x01 (port 1)
        out = parse(v3([(4, 1, 0), (2, 1, 0)], b"\x00\x10\x00\x01\x10\x00\xfd"))
        self.assertEqual(out["writes"], [2, 0])
        # device 2 occupies bits 1..6 -> command 0x04
        out = parse(v3([(4, 1, 0), (2, 1, 0), (3, 1, 0)], b"\x04\x20\x00\xfd"))
        self.assertEqual(out["writes"], [0, 0, 1])

    def test_port_bit_is_not_device(self):
        # odd command byte must not land on device 1
        out = parse(v3([(4, 1, 0), (2, 1, 0)], b"\x01\x30\x01\xfd"))
        self.assertEqual(out["writes"], [1, 0])

    def test_device_out_of_range(self):
        with self.assertRaises(S98Error):
            parse(v3([(4, 1, 0)], b"\x02\x00\x00\xfd"))   # device index 1, only one device


class LoopTests(unittest.TestCase):
    def test_loop_point_in_syncs(self):
        # loop offset lands after the first two syncs
        body = b"\xff\xff\xfd"
        loop = V3_HEADER + ENTRY + 2
        out = parse(v3([(4, 1, 0)], body, loop_off=loop))
        self.assertEqual(out["loop_seconds"], "1/50")

    def test_loop_with_writes(self):
        # 3-byte write then 0xFF: loop lands after one write+sync (4 bytes in)
        body = b"\x00\x10\x00\xff\x00\x10\x00\xff\xfd"
        loop = V3_HEADER + ENTRY + 4
        out = parse(v3([(4, 1, 0)], body, loop_off=loop))
        self.assertEqual(out["syncs"], 2)
        self.assertEqual(out["loop_seconds"], "1/100")

    def test_no_loop(self):
        self.assertIsNone(parse(v3([(4, 1, 0)], b"\xff\xfd"))["loop_seconds"])

    def test_end_marker(self):
        self.assertTrue(parse(v3([(4, 1, 0)], b"\xfd"))["end_marker"])
        self.assertFalse(parse(v3([(4, 1, 0)], b"\xff"))["end_marker"])


class TagTests(unittest.TestCase):
    def _with_tag(self, raw):
        body = b"\xfd"
        dump = V3_HEADER + ENTRY
        tag = dump + len(body)
        log = v3([(4, 1, 0)], body, tag_off=tag) + raw + b"\x00"
        return parse(log)

    def test_s98_keyvalue_utf8(self):
        raw = "[S98]\nTitle=Field of Wind\nARTIST=Falcom\nGame=Ys\n".encode("utf-8")
        self.assertEqual(self._with_tag(raw)["tags"],
                         {"title": "Field of Wind", "artist": "Falcom", "game": "Ys"})

    def test_utf8_bom_stripped(self):
        raw = b"[S98]\xef\xbb\xbftitle=X\n"
        self.assertEqual(self._with_tag(raw)["tags"], {"title": "X"})

    def test_shift_jis(self):
        raw = "[S98]\ntitle=".encode("ascii") + "風の伝説".encode("shift_jis") + b"\n"
        self.assertEqual(self._with_tag(raw)["tags"], {"title": "風の伝説"})

    def test_old_style_tag_is_title(self):
        self.assertEqual(self._with_tag(b"Opening")["tags"], {"title": "Opening"})

    def test_old_style_shift_jis_title(self):
        raw = "オープニング".encode("shift_jis")
        self.assertEqual(self._with_tag(raw)["tags"], {"title": "オープニング"})

    def test_comments_and_blank_lines(self):
        raw = b"[S98]\n# a comment\n\ntitle=T\njunkline\nartist=A\r\n"
        self.assertEqual(self._with_tag(raw)["tags"], {"title": "T", "artist": "A"})

    def test_no_tags(self):
        self.assertEqual(parse(v3([(4, 1, 0)], b"\xfd"))["tags"], {})


if __name__ == "__main__":
    unittest.main()
