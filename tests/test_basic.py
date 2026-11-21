import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mux import Mux, pes, pmt, pat, simple  # noqa: E402
from tskit.ts import TsError, scan  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_program(self):
        out = scan(simple(pcr=False).data())
        prog = out["programs"][0]
        self.assertEqual((prog["program"], prog["pmt_pid"], prog["pcr_pid"]), (1, 0x100, 0x101))
        video, audio = prog["streams"]
        self.assertEqual((video["stream_type"], video["pes"], video["first_pts"], video["last_pts"]), ("h264", 4, 0, 9009))
        self.assertEqual(audio["duration"], "16/375")
        self.assertEqual(out["cc_errors"], 0)

    def test_no_sync(self):
        with self.assertRaises(TsError):
            scan(b"\x00" * 400)

    def test_resync_after_garbage(self):
        m = simple(pcr=False)
        data = m.data()
        # Splice junk bytes into the middle of the capture.
        cut = 188 * 3
        damaged = data[:cut] + b"\x00\x11\x22" * 13 + b"\x00" + data[cut:]
        out = scan(damaged)
        video = out["programs"][0]["streams"][0]
        self.assertEqual((video["pes"], video["first_pts"], video["last_pts"]), (4, 0, 9009))
        self.assertEqual(out["packets"], len(m.out))        # all packets recovered
        self.assertEqual(out["cc_errors"], 0)               # junk is not a packet

    def test_resync_m2ts_keeps_prefix(self):
        m = simple(pcr=False)
        data = m.data(m2ts=True)
        cut = 192 * 3
        damaged = data[:cut] + b"\xaa" * 192 + data[cut + 192:]
        out = scan(damaged)
        self.assertEqual(out["packet_size"], 192)
        self.assertEqual(out["packets"], len(m.out) - 1)

    def test_bluray_packet_size(self):
        out = scan(simple().data(m2ts=True))
        self.assertEqual(out["packet_size"], 192)
        self.assertEqual(out["packets"], len(simple().out))
        self.assertEqual(out["cc_errors"], 0)
        self.assertEqual(out["programs"][0]["streams"][0]["last_pts"], 9009)

    def test_dvb_packet_size(self):
        out = scan(simple().data(rs=True))
        self.assertEqual(out["packet_size"], 204)
        self.assertEqual(out["packets"], len(simple().out))
        self.assertEqual(out["programs"][0]["streams"][0]["duration"], "1001/10000")

    def test_pmt_spanning_two_packets(self):
        m = Mux()
        m.psi(0, pat([(1, 0x100)]))
        # Enough descriptors to push the PMT past one 184-byte packet.
        desc = bytes([0x0A, 200]) + b"x" * 200
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, desc), (0x0F, 0x102, b""), (0x03, 0x103, b"")]))
        for pid, sid in ((0x101, 0xE0), (0x102, 0xC0), (0x103, 0xC0)):
            m.stream(pid, pes(sid, 1000, size=100))
        out = scan(m.data())
        streams = out["programs"][0]["streams"]
        self.assertEqual([s["pid"] for s in streams], [0x101, 0x102, 0x103])
        self.assertEqual([s["pes"] for s in streams], [1, 1, 1])
        self.assertEqual([s["first_pts"] for s in streams], [1000, 1000, 1000])

    def test_pts_wraparound(self):
        top = (1 << 33) - 9000
        pts_values = (top, top + 3003, 1000, 5000)
        out = scan(simple(video_pts=pts_values, audio_pts=(), pcr=False).data())
        video = out["programs"][0]["streams"][0]
        self.assertEqual(video["first_pts"], top)
        self.assertEqual(video["last_pts"], 5000)
        self.assertEqual(video["duration"], str(__import__("fractions").Fraction(14000, 90000)))

    def test_pcr_pid_no_cc_errors(self):
        # PCR-only packets (adaptation field, no payload) do not increment the
        # continuity counter; they must not be reported as discontinuities.
        m = Mux()
        m.psi(0, pat([(1, 0x100)]))
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]))
        for v in (0, 3003, 6006):
            m.packet(0x101, pcr=v * 300, af=True)
            m.packet(0x101, pcr=v * 300, af=True, afc=2)
            m.stream(0x101, pes(0xE0, v, size=100))
        out = scan(m.data())
        self.assertEqual(out["cc_errors"], 0)
        self.assertEqual(out["programs"][0]["streams"][0]["cc_errors"], 0)
        self.assertEqual(out["pcr"], 6)

    def test_bframes_pts_dts(self):
        m = Mux()
        m.psi(0, pat([(1, 0x100)]))
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]))
        # Decode order 0, 3, 1, 2 -- every PES has both PTS and DTS.
        for dts, pt in ((0, 0), (3003, 9009), (6006, 3003), (9009, 6006)):
            m.stream(0x101, pes(0xE0, pt, dts, size=100))
        video = scan(m.data())["programs"][0]["streams"][0]
        self.assertEqual((video["pes"], video["first_pts"], video["last_pts"]), (4, 0, 6006))
        self.assertEqual(video["duration"], "1001/15000")

    def test_bad_section_crc(self):
        m = Mux()
        bad = bytearray(pat([(1, 0x100)]))
        bad[-1] ^= 0xFF
        m.psi(0, bytes(bad))
        m.psi(0, pat([(1, 0x100)]))
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]))
        m.stream(0x101, pes(0xE0, 0, size=100))
        out = scan(m.data())
        self.assertEqual(out["bad_sections"], 1)
        self.assertEqual(out["programs"][0]["streams"][0]["pes"], 1)

    def test_tei_and_null(self):
        m = simple()
        m.packet(0x1FFF, payload=b"\xff" * 100)
        m.packet(0x102, payload=b"\xff" * 20, tei=True)
        out = scan(m.data())
        self.assertEqual(out["tei"], 1)
        self.assertEqual(out["null"], 1)
        self.assertEqual(out["packets"], len(m.out))

    def test_pes_header_split_across_packets(self):
        # Only the first 6 bytes of the PES header fit in the first packet, so
        # the PTS arrives in the second one.
        m = Mux()
        m.psi(0, pat([(1, 0x100)]))
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]))
        body = pes(0xE0, 9000, size=400)
        m.packet(0x101, body[:6], pusi=True)
        rest = body[6:]
        while rest:
            m.packet(0x101, rest[:184])
            rest = rest[184:]
        out = scan(m.data())
        video = out["programs"][0]["streams"][0]
        self.assertEqual((video["pes"], video["first_pts"], video["last_pts"]), (1, 9000, 9000))

    def test_section_completed_by_pointer_field(self):
        # A section split over two packets, then a packet whose pointer_field
        # finishes it and starts a second, complete section.
        m = Mux()
        section = pmt(1, 0x101, [(0x1B, 0x101, b""), (0x0F, 0x102, b"")])
        head, tail = section[:6], section[6:]
        m.psi(0, pat([(1, 0x100)]))
        m.packet(0x100, bytes([0]) + head, pusi=True)
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]), pointer_tail=tail)
        out = scan(m.data())
        self.assertEqual(out["bad_sections"], 0)
        self.assertEqual([s["pid"] for s in out["programs"][0]["streams"]], [0x101])

    def test_dvb_packet_size_with_garbage(self):
        m = simple(pcr=False)
        data = m.data(rs=True)
        cut = 204 * 3
        damaged = data[:cut] + b"\x55" * 37 + data[cut:]
        out = scan(damaged)
        self.assertEqual(out["packet_size"], 204)
        self.assertEqual(out["packets"], len(m.out))
        self.assertEqual(out["programs"][0]["streams"][0]["last_pts"], 9009)

    def test_cc_error_detection(self):
        m = Mux()
        m.psi(0, pat([(1, 0x100)]))
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]))
        m.packet(0x101, pes(0xE0, 0, size=400)[:184], pusi=True, cc=0)
        m.packet(0x101, pes(0xE0, 0, size=400)[184:184 + 50], cc=3)
        out = scan(m.data())
        self.assertEqual(out["cc_errors"], 1)
        self.assertEqual(out["programs"][0]["streams"][0]["cc_errors"], 1)

    def test_duplicate_packet_is_not_error(self):
        m = Mux()
        m.psi(0, pat([(1, 0x100)]))
        m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b"")]))
        m.packet(0x101, pes(0xE0, 0, size=100)[:184], pusi=True)
        duplicate = m.out[-1]
        m.out.append(duplicate)
        m.stream(0x101, pes(0xE0, 3003, size=100))
        out = scan(m.data())
        self.assertEqual(out["cc_errors"], 0)
        self.assertEqual(out["programs"][0]["streams"][0]["last_pts"], 3003)


if __name__ == "__main__":
    unittest.main()
