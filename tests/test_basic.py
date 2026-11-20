import struct
import unittest

from aviscan import clips
from aviscan.index import scan
from aviscan.riff import walk


def chunk(cid, payload):
    body = cid.encode("latin-1") + struct.pack("<I", len(payload)) + payload
    return body + (b"\0" if len(payload) & 1 else b"")


def list_chunk(kind, payload):
    return chunk("LIST", kind.encode("latin-1") + payload)


def make_avi(frames, scale=1, rate=25, idx1_base="movi", junk_odd=True,
             odml=False):
    """Build a minimal AVI.

    *frames* is a list of ``(size, key)``; payload bytes hold the frame
    number so index entries can be checked against the file contents.
    ``idx1_base`` selects the offset convention: ``movi`` (documented),
    ``file`` (file start) or ``data`` (inside the chunk, movi relative).
    """
    strh = struct.pack("<4s4sIHHIIIIIIIIhhhh",
                       b"vids", b"H264", 0, 0, 0, 0, scale, rate, 0,
                       len(frames), 0, 0, 0, 0, 0, 0, 0)
    strf = struct.pack("<IiiHH4sIiiII", 40, 16, 16, 1, 24, b"H264", 0, 0, 0, 0, 0)
    avih = struct.pack("<14I", 40000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    strl = list_chunk("strl", chunk("strh", strh) + chunk("strf", strf))
    if odml:
        # Placeholder for the super index; the real value is patched in
        # below once the movi list position is known.  Same byte count.
        strl += chunk("indx", b"\0" * 40)
    hdrl = list_chunk("hdrl", chunk("avih", avih) + strl)
    if junk_odd:
        hdrl += chunk("JUNK", b"abc")      # odd size: needs a pad byte

    movi_fourcc = 12 + len(hdrl) + 8
    payload = b"".join(chunk("00dc", bytes([n % 256]) * size)
                       for n, (size, _) in enumerate(frames))

    # Absolute positions of the chunk headers and their data.
    headers = []
    pos = 4                                # first child, just after 'movi'
    for size, _ in frames:
        headers.append(movi_fourcc + pos)
        pos += 8 + size + (size & 1)

    if idx1_base == "movi":
        offsets = [h - movi_fourcc for h in headers]
    elif idx1_base == "file":
        offsets = headers
    else:                                  # "data": payload, movi relative
        offsets = [h + 8 - movi_fourcc for h in headers]

    idx1 = b"" if odml else chunk("idx1", b"".join(
        struct.pack("<4sIII", b"00dc", 0x10 if key else 0, off, size)
        for (size, key), off in zip(frames, offsets)))

    if odml:
        # Standard index in its own ix00 chunk at the end of movi.
        # qwBaseOffset is the 'movi' fourcc and dwOffset counts up to the
        # chunk data from there.
        ix00_payload = struct.pack("<HBBI4sQI", 2, 0, 1, len(frames),
                                   b"00dc", movi_fourcc, 0)
        for (size, key), h in zip(frames, headers):
            ix00_payload += struct.pack(
                "<II", h + 8 - movi_fourcc, size | (0x80000000 if key else 0))
        ix00 = chunk("ix00", ix00_payload)
        ix00_pos = movi_fourcc + 4 + len(payload)

        # Patch the super index (AVISUPERINDEX) into strl: 24-byte header
        # plus one 16-byte entry.
        indx = struct.pack("<HBBI4sIII", 4, 0, 0, 1, b"00dc", 0, 0, 0)
        indx += struct.pack("<QII", ix00_pos, len(ix00), len(frames))
        assert len(indx) == 40
        strl = list_chunk("strl", chunk("strh", strh) + chunk("strf", strf)
                          + chunk("indx", indx))
        hdrl = list_chunk("hdrl", chunk("avih", avih) + strl)
        if junk_odd:
            hdrl += chunk("JUNK", b"abc")
        movi = list_chunk("movi", payload + ix00)
    else:
        movi = list_chunk("movi", payload)

    body = b"AVI " + hdrl + movi + idx1
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_segmented_avi(segments, scale=1, rate=25):
    """Build an OpenDML-style file split over RIFF/AVIX segments.

    Each segment carries its own ``movi`` list and ``idx1`` whose offsets are
    relative to that segment's 'movi' fourcc, as the AVI spec requires.
    """
    strh = struct.pack("<4s4sIHHIIIIIIIIhhhh",
                       b"vids", b"H264", 0, 0, 0, 0, scale, rate, 0,
                       sum(len(s) for s in segments), 0, 0, 0, 0, 0, 0, 0)
    strf = struct.pack("<IiiHH4sIiiII", 40, 16, 16, 1, 24, b"H264", 0, 0, 0, 0, 0)
    avih = struct.pack("<14I", 40000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    hdrl = list_chunk("hdrl", chunk("avih", avih) + list_chunk(
        "strl", chunk("strh", strh) + chunk("strf", strf)))

    out = []
    number = 0
    for n, frames in enumerate(segments):
        payload = b""
        offsets = []
        pos = 4                            # first child, after 'movi'
        for size, key in frames:
            offsets.append(pos)
            payload += chunk("00dc", bytes([number % 256]) * size)
            number += 1
            pos += 8 + size + (size & 1)
        idx1 = chunk("idx1", b"".join(
            struct.pack("<4sIII", b"00dc", 0x10 if key else 0, off, size)
            for (size, key), off in zip(frames, offsets)))
        kind = b"AVI " if n == 0 else b"AVIX"
        body = kind + (hdrl if n == 0 else b"") + list_chunk("movi", payload) + idx1
        out.append(b"RIFF" + struct.pack("<I", len(body)) + body)
    return b"".join(out)


class BasicTests(unittest.TestCase):
    def test_walk(self):
        data = b"RIFF" + (20).to_bytes(4, "little") + b"AVI " + b"JUNK" + (4).to_bytes(4, "little") + b"abcd"
        top = walk(data, 0, len(data))[0]
        self.assertEqual((top.list_type, [c.id for c in top.children]), ("AVI ", ["JUNK"]))

    def test_walk_odd_junk(self):
        # A 7-byte JUNK chunk must not misalign the following chunks.
        data = make_avi([(10, True), (11, False)])
        top = walk(data, 0, len(data))[0]
        self.assertEqual([c.id for c in top.children],
                         ["LIST", "JUNK", "LIST", "idx1"])

    def test_bounds(self):
        self.assertEqual(clips.bounds([0, 25, 50], 30, 60), (25, 60))
        self.assertEqual(clips.key_times({"rate": "25"}, [0, 25]), ["0", "1"])


class IndexTests(unittest.TestCase):
    def test_idx1_offsets_are_data_positions(self):
        data = make_avi([(800, True), (121, False), (122, False)])
        s = scan(data)[0]
        self.assertEqual(s["chunks"], 3)
        self.assertEqual(s["keyframes"], 1)
        for n, e in enumerate(s["first"]):
            self.assertEqual(data[e["offset"]:e["offset"] + 4],
                             bytes([n]) * 4)
            self.assertEqual(data[e["offset"] - 8:e["offset"] - 4], b"00dc")
            self.assertEqual(struct.unpack("<I", data[e["offset"] - 4:e["offset"]])[0],
                             e["size"])

    def test_idx1_file_relative_offsets(self):
        m = make_avi([(64, True), (65, False)], idx1_base="movi")
        f = make_avi([(64, True), (65, False)], idx1_base="file")
        self.assertEqual(scan(m)[0]["first"], scan(f)[0]["first"])

    def test_idx1_data_relative_offsets(self):
        m = make_avi([(64, True), (65, False)], idx1_base="movi")
        f = make_avi([(64, True), (65, False)], idx1_base="data")
        self.assertEqual(scan(m)[0]["first"], scan(f)[0]["first"])

    def test_rate_from_stream_header(self):
        s = scan(make_avi([(10, True)], scale=1001, rate=30000))[0]
        self.assertEqual(s["rate"], "30000/1001")

    def test_segmented_idx1(self):
        data = make_segmented_avi([[(100, True), (101, False)],
                                   [(102, True), (103, False)]])
        s = scan(data)[0]
        self.assertEqual(s["chunks"], 4)
        self.assertEqual(s["keyframes"], 2)
        self.assertEqual([e["key"] for e in s["first"]], [True, False, True])
        # Every entry points at the real payload of its own segment.
        from aviscan.index import entries
        riffs = [c for c in walk(data, 0, len(data)) if c.id == "RIFF"]
        for n, e in enumerate(entries(data, riffs)[0]):
            self.assertEqual(data[e["offset"]:e["offset"] + 4], bytes([n]) * 4)

    def test_no_index(self):
        # Same file without any index: an idx1-less AVI with no indx.
        data = make_avi([(10, True), (11, False)], odml=True)
        data = data.replace(b"indx", b"JUNK").replace(b"ix00", b"JUNK")
        s = scan(data)[0]
        self.assertEqual((s["chunks"], s["keyframes"]), (0, 0))

    def test_opendml_index(self):
        frames = [(900, False), (201, True), (202, True), (203, False)]
        data = make_avi(frames, scale=1001, rate=30000, odml=True)
        s = scan(data)[0]
        self.assertEqual(s["chunks"], 4)
        self.assertEqual(s["keyframes"], 2)
        self.assertEqual([e["key"] for e in s["first"]],
                         [False, True, True])
        for n, e in enumerate(s["first"]):
            self.assertEqual(data[e["offset"]:e["offset"] + 4],
                             bytes([n]) * 4)
            self.assertEqual(struct.unpack("<I", data[e["offset"] - 4:e["offset"]])[0],
                             e["size"])
        self.assertEqual(s["rate"], "30000/1001")
        self.assertEqual(s["duration"], "1001/7500")   # 4 frames at 30000/1001

    def test_bogus_second_segment_size(self):
        # Recorders sometimes fill the second RIFF header with 0xFFFFFFFF;
        # the segment must still be found.
        good = make_segmented_avi([[(100, True)], [(101, True)]])
        second = good.find(b"RIFF", 4)
        bad = bytearray(good)
        bad[second + 4:second + 8] = b"\xff\xff\xff\xff"
        s = scan(bytes(bad))[0]
        self.assertEqual((s["chunks"], s["keyframes"]), (2, 2))

    def test_palette_change_chunks_not_counted(self):
        data = make_avi([(10, True), (11, False)])
        idx1 = data.find(b"idx1")
        extra = struct.pack("<4sIII", b"00pc", 0, 4, 4)
        data = data[:idx1 + 4] + struct.pack("<I", 2 * 16 + 16) + \
            data[idx1 + 8:] + extra
        s = scan(data)[0]
        self.assertEqual((s["chunks"], s["keyframes"]), (2, 1))


class ExampleTests(unittest.TestCase):
    """The two sample recordings shipped with the repository."""

    def load(self, name):
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "examples", name), "rb") as fh:
            return fh.read()

    def check(self, name, chunks, keys, rate, duration):
        data = self.load(name)
        s = scan(data)[0]
        self.assertEqual((s["chunks"], s["keyframes"]), (chunks, keys))
        self.assertEqual(s["rate"], rate)
        self.assertEqual(s["duration"], duration)
        for e in s["first"]:
            # offset is the absolute position of the chunk data
            self.assertEqual(data[e["offset"] - 8:e["offset"] - 4], b"00dc")
            self.assertEqual(struct.unpack("<I", data[e["offset"] - 4:e["offset"]])[0],
                             e["size"])

    def test_lobby_idx1(self):
        # 25 fps, one key frame every 25 frames, odd JUNK after hdrl.
        self.check("cam01_lobby.avi", 250, 10, "25", "10")

    def test_dock_opendml(self):
        self.check("cam07_dock_odml.avi", 180, 174, "30000/1001", "3003/500")


if __name__ == "__main__":
    unittest.main()
