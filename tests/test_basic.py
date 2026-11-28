import struct
import unittest

from toonkit.toons import ToonError, Toons, parse


def sprite_block(sid, w, h, lines, end_frame=999):
    data = struct.pack(">HH", h, w) + bytes(10)
    for ln in lines:
        data += struct.pack(">H", len(ln)) + bytes(ln)
    return struct.pack(">HHIHHH", sid, 0, 14 + len(data), 0, end_frame, 0) + data


def palette_block(sid, entries, end_frame=999):
    data = b"".join(struct.pack(">HHH", r, g, b) for r, g, b in entries)
    return struct.pack(">HHIHHH", sid, 0, 14 + len(data), 0, end_frame, 0) + data


def diff_entry(x, y, w, h, lines):
    data = struct.pack(">HH", h, w) + bytes(10)
    for ln in lines:
        data += struct.pack(">H", len(ln)) + bytes(ln)
    return b"Diff" + struct.pack(">I", 12 + len(data)) + struct.pack(">hh", x, y) + data


def frame_buf(fid, background=0, refs=(), sprites=(), entries=b"", pal_id=0, pal_set=0):
    hdr = bytearray(44)
    struct.pack_into(">H", hdr, 2, fid)
    hdr[7] = background
    struct.pack_into(">H", hdr, 28, len(sprites))
    reflist = b"".join(struct.pack(">HhhHH", sid, top, left, 0, right)
                       for sid, top, left, right in refs)
    struct.pack_into(">H", hdr, 30, 44 + len(reflist))
    hdr[34] = len(refs)
    struct.pack_into(">H", hdr, 36, pal_id)
    hdr[38] = pal_set
    return bytes(hdr) + reflist + b"".join(sprites) + entries


def pack(w, h, frames):
    out = b"TOONPACK" + struct.pack(">HHH", w, h, len(frames))
    for buf in frames:
        out += struct.pack(">I", len(buf)) + buf
    return out


class BasicTests(unittest.TestCase):
    def test_example_frames(self):
        with open("examples/cartoon.toon", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 32, 4))

    def test_bad_magic(self):
        with self.assertRaises(ToonError):
            parse(bytes(16))


class RenderTests(unittest.TestCase):
    def test_run_segment_full_length_and_transparency(self):
        # run of 3 transparent, run of 2 colour 5: exactly 5 pixels, rest background
        t = Toons(8, 2)
        t.frame(frame_buf(1, background=7, refs=[(1, 0, 0, 0)],
                          sprites=[sprite_block(1, 8, 1, [[0x82, 0x00, 0x81, 0x05]])]))
        self.assertEqual(list(t.pix[0:8]), [7, 7, 7, 5, 5, 7, 7, 7])

    def test_frame_cleared_to_background(self):
        sprite = sprite_block(1, 2, 1, [[0x81, 0x05]])
        t = Toons(8, 1)
        t.frame(frame_buf(1, background=7, refs=[(1, 0, 0, 0)], sprites=[sprite]))
        self.assertEqual(list(t.pix[0:4]), [5, 5, 7, 7])
        # same sprite moves right: no trail left behind
        t.frame(frame_buf(2, background=7, refs=[(1, 0, 4, 0)]))
        self.assertEqual(list(t.pix), [7, 7, 7, 7, 5, 5, 7, 7])

    def test_duplicate_sprite_id_replaces(self):
        t = Toons(4, 1)
        t.frame(frame_buf(1, refs=[(1, 0, 0, 0)],
                          sprites=[sprite_block(1, 2, 1, [[0x81, 0x05]])]))
        t.frame(frame_buf(2, refs=[(1, 0, 0, 0)],
                          sprites=[sprite_block(1, 2, 1, [[0x81, 0x06]])]))
        self.assertEqual(list(t.pix[0:2]), [6, 6])

    def test_sprite_lifetime_expires(self):
        t = Toons(4, 1)
        t.frame(frame_buf(1, refs=[(1, 0, 0, 0)],
                          sprites=[sprite_block(1, 2, 1, [[0x81, 0x05]], end_frame=1)]))
        self.assertIn(1, t.sprites)
        t.frame(frame_buf(2, refs=[(1, 0, 0, 0)]))
        self.assertNotIn(1, t.sprites)
        self.assertEqual(list(t.pix), [0, 0, 0, 0])

    def test_diff_entry_drawn_on_top(self):
        t = Toons(8, 2)
        t.frame(frame_buf(1, background=7, refs=[(1, 0, 0, 0)],
                          sprites=[sprite_block(1, 8, 1, [[0x87, 0x03]])],
                          entries=diff_entry(2, 1, 2, 1, [[0x01, 0x09, 0x09]])))
        self.assertEqual(list(t.pix[0:8]), [3] * 8)
        self.assertEqual(list(t.pix[8:16]), [7, 7, 9, 9, 7, 7, 7, 7])

    def test_palette_uses_high_bytes(self):
        t = Toons(1, 1)
        t.frame(frame_buf(1, pal_id=100,
                          sprites=[palette_block(100, [(0x1234, 0xAB00, 0x00FF)])]))
        self.assertEqual(t.pal[0], (0x12, 0xAB, 0x00))

    def test_pack_with_diff_and_reused_ids_decodes(self):
        frames = [
            frame_buf(1, background=2, refs=[(1, 0, 0, 0)],
                      sprites=[sprite_block(1, 2, 1, [[0x81, 0x05]])]),
            frame_buf(2, background=2, refs=[(1, 0, 1, 0)],
                      sprites=[sprite_block(1, 2, 1, [[0x81, 0x06]])],
                      entries=diff_entry(0, 0, 1, 1, [[0x00, 0x04]])),
        ]
        out = parse(pack(4, 1, frames))
        self.assertEqual([f["state"] for f in out["frames"]], ["decoded", "decoded"])


if __name__ == "__main__":
    unittest.main()
