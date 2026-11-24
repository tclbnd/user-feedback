import struct
import unittest
from fractions import Fraction

from s98kit.s98 import S98Error, parse

LOG = b"S983" + struct.pack("<6I", 0, 0, 0, 0, 0x30, 0) + struct.pack("<I", 1) + struct.pack("<4I", 4, 7987200, 0, 0) + b"\xfd"


def device(dev_type, clock=3993600, pan=0):
    return struct.pack("<4I", dev_type, clock, pan, 0)


def build(version="3", devices=(), dump=b"", num=0, den=0, loop_rel=None, tag=b""):
    """Assemble a well-formed log, deriving dump/tag offsets from the layout."""
    table = 4 + 16 * len(devices) if version == "3" else 0
    # v3 dumps start at 0x40 or later (the header reserves room for the device table).
    dump_off = max(0x20 + table, 0x40) if version == "3" else 0x20
    # A zero tag offset means "no tag block"; a NUL separator precedes the tag text.
    tag_off = dump_off + len(dump) + 1 if tag else 0
    loop = dump_off + loop_rel if loop_rel is not None else 0
    head = b"S98" + version.encode("ascii")
    head += struct.pack("<6I", num, den, 0, tag_off, dump_off, loop)
    if version == "3":
        head += struct.pack("<I", len(devices))
        head += b"".join(device(*d) if isinstance(d, tuple) else d for d in devices)
    head += b"\x00" * (dump_off - len(head))
    return head + dump + (b"\x00" + tag if tag else b"")


class BasicTests(unittest.TestCase):
    def test_device(self):
        out = parse(LOG)
        self.assertEqual((out["version"], out["devices"][0]["type"], out["end_marker"]), ("3", "ym2608", True))

    def test_empty(self):
        self.assertEqual(parse(LOG)["syncs"], 0)


class HeaderTests(unittest.TestCase):
    def test_default_tick_is_num_over_den(self):
        out = parse(build(dump=b"\xfd"))
        self.assertEqual(out["tick"], "1/100")
        self.assertEqual(out["duration"], "0")

    def test_explicit_timer(self):
        out = parse(build(dump=b"\xfd", num=1, den=60))
        self.assertEqual(out["tick"], "1/60")

    def test_version_2(self):
        out = parse(build(version="2", dump=b"\xfd"))
        self.assertEqual(out["version"], "2")
        self.assertEqual([d["type"] for d in out["devices"]], ["ym2203", "ym2203"])
        self.assertTrue(all(d["clock"] == 3993600 for d in out["devices"]))


class SyncTests(unittest.TestCase):
    def test_single_sync(self):
        out = parse(build(dump=b"\xff" * 3 + b"\xfd"))
        self.assertEqual((out["syncs"], out["duration"]), (3, "3/100"))

    def test_multi_sync_minimum_adds_two(self):
        # 0xFE with a zero byte means 0 + 2 = 2 syncs
        out = parse(build(dump=b"\xfe\x00\xfd"))
        self.assertEqual((out["syncs"], out["duration"]), (2, "1/50"))

    def test_multi_sync_groups_least_significant_first(self):
        # groups 0x83 0x82 0x01 -> 3 + (2 << 7) + (1 << 14), then the spec adds 2
        count = 3 + (2 << 7) + (1 << 14) + 2
        out = parse(build(dump=b"\xfe\x83\x82\x01\xfd"))
        self.assertEqual(out["syncs"], count)
        self.assertEqual(out["duration"], str(Fraction(count, 100)))

    def test_long_pause(self):
        # 0xFE 0x86 0x27 -> (6) + (39 << 7) = 4998, plus 2 = 5000; at 10 ms each = 50 s
        out = parse(build(dump=b"\xfe\x86\x27\xfd"))
        self.assertEqual(out["syncs"], 5000)
        self.assertEqual(out["duration"], "50")


class WriteTests(unittest.TestCase):
    def test_device_index_is_low_nibble(self):
        dump = b"".join(bytes((i, 0x10, 0x20)) for i in range(4)) + b"\xfd"
        out = parse(build(devices=((2,), (4,), (5,), (9,)), dump=dump))
        self.assertEqual(out["writes"], [1, 1, 1, 1])

    def test_v1_two_devices(self):
        # v1/v2 logs have no device table but writes 0x00 and 0x01 must both work
        dump = b"\x00\x28\xf0" + b"\x01\x00\x30" + b"\xfd"
        out = parse(build(version="1", dump=dump))
        self.assertEqual(out["writes"], [1, 1])

    def test_unknown_device_type(self):
        out = parse(build(devices=((12,),), dump=b"\xfd"))
        self.assertEqual(out["devices"][0]["type"], "type_12")

    def test_write_beyond_devices_rejected(self):
        with self.assertRaises(S98Error):
            parse(build(devices=((2,),), dump=b"\x01\x00\x00\xfd"))


class LoopTests(unittest.TestCase):
    def test_loop_point_time(self):
        # pure sync dump: FF FF FF FD; loop at the second FF means 1 tick elapsed
        dump = b"\xff\xff\xff\xfd"
        out = parse(build(dump=dump, loop_rel=1))
        self.assertEqual(out["syncs"], 3)
        self.assertEqual(out["loop_seconds"], "1/100")

    def test_loop_after_one_sync(self):
        out = parse(build(dump=b"\xff\xff\xfd", loop_rel=1))
        self.assertEqual(out["loop_seconds"], "1/100")

    def test_loop_at_multi_sync_command(self):
        # loop at the FE command itself: 0 ticks elapsed when the pause begins
        out = parse(build(dump=b"\xfe\x00\xfd", loop_rel=0))
        self.assertEqual(out["loop_seconds"], "0")
        self.assertEqual(out["syncs"], 2)

    def test_no_loop_is_null(self):
        self.assertIsNone(parse(build(dump=b"\xff\xfd"))["loop_seconds"])

    def test_loop_at_first_command(self):
        out = parse(build(dump=b"\xff\xfd", loop_rel=0))
        self.assertEqual(out["loop_seconds"], "0")

    def test_loop_with_devices(self):
        prefix = b"\x01\x10\x20" + b"\xff"                        # write dev 1, then 1 sync
        out = parse(build(devices=((2,), (4,)), dump=prefix + b"\xfd", loop_rel=len(prefix)))
        self.assertEqual(out["writes"], [0, 1])
        self.assertEqual(out["loop_seconds"], "1/100")


class TagTests(unittest.TestCase):
    def test_s98_tag_block(self):
        # Common writer layout: BOM sits between the [S98] header and the first key
        tag = b"[S98]\xef\xbb\xbftitle=Field of Wind\nartist=Falcom\x00"
        out = parse(build(dump=b"\xfd", tag=tag))
        self.assertEqual(out["tags"], {"title": "Field of Wind", "artist": "Falcom"})

    def test_s98_tag_block_without_bom(self):
        tag = b"[S98]\ntitle=Field of Wind\nartist=Falcom\x00"
        out = parse(build(dump=b"\xfd", tag=tag))
        self.assertEqual(out["tags"], {"title": "Field of Wind", "artist": "Falcom"})

    def test_old_style_tag_is_title(self):
        out = parse(build(version="1", dump=b"\xfd", tag=b"Opening\x00"))
        self.assertEqual(out["tags"], {"title": "Opening"})

    def test_no_tags(self):
        self.assertEqual(parse(build(dump=b"\xfd"))["tags"], {})


if __name__ == "__main__":
    unittest.main()
