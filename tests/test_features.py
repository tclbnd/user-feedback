"""Requirements from the task: jitter, duplicates, reordering, RTCP, link layers and the bug fixes."""
import struct
import unittest
from fractions import Fraction

from rtpkit.rtp import parse, RtpError, rtp_packet

from caps import rtp, udp, ipv4, ipv6, hbh, fragment, ethernet, sll, sll2, loopback, pcap


def one(packets, **kwargs):
    frames = [(Fraction(i + 1) / 10, ethernet(ipv4(udp(p)))) for i, p in enumerate(packets)]
    return parse(pcap(frames, **kwargs))["streams"][0]


class MagicTests(unittest.TestCase):
    def test_big_endian_microsecond(self):
        d = pcap([(1, ethernet(ipv4(udp(rtp()))))], big=True)
        self.assertEqual(d[:4], b"\xa1\xb2\xc3\xd4")
        self.assertEqual(parse(d)["link_type"], 1)

    def test_little_endian_nanosecond(self):
        d = pcap([(1, ethernet(ipv4(udp(rtp()))))], nano=True)
        out = parse(d)
        self.assertEqual(d[:4], b"\x4d\x3c\xb2\xa1")
        self.assertTrue(out["nanosecond"])

    def test_big_endian_nanosecond(self):
        d = pcap([(1, ethernet(ipv4(udp(rtp()))))], big=True, nano=True)
        out = parse(d)
        self.assertEqual(d[:4], b"\xa1\xb2\x3c\x4d")
        self.assertTrue(out["nanosecond"])


class VlanTests(unittest.TestCase):
    def test_single_vlan(self):
        d = pcap([(1, ethernet(ipv4(udp(rtp())), vlans=(0x8100,)))])
        self.assertEqual(parse(d)["streams"][0]["ssrc"], "cafe0001")

    def test_double_qinq(self):
        d = pcap([(1, ethernet(ipv4(udp(rtp())), vlans=(0x88A8, 0x8100)))])
        self.assertEqual(len(parse(d)["streams"]), 1)


class SequenceTests(unittest.TestCase):
    def test_wrap_without_loss(self):
        pkts = [rtp(seq=(65534 + i) & 0xFFFF) for i in range(4)]   # ...534,535,0,1
        s = one(pkts)
        self.assertEqual((s["packets"], s["lost"], s["duplicates"], s["out_of_order"]), (4, 0, 0, 0))

    def test_loss_after_wrap(self):
        pkts = [rtp(seq=65535), rtp(seq=0), rtp(seq=2)]
        s = one(pkts)
        self.assertEqual((s["packets"], s["lost"]), (3, 1))

    def test_duplicate_and_reorder(self):
        pkts = [rtp(seq=1), rtp(seq=3), rtp(seq=2), rtp(seq=2), rtp(seq=4)]
        s = one(pkts)
        self.assertEqual((s["packets"], s["duplicates"], s["out_of_order"], s["lost"]), (4, 1, 2, 0))


class JitterTests(unittest.TestCase):
    def test_zero_jitter_on_metered_stream(self):
        # 20 ms apart, 160 timestamp units apart at 8 kHz: D(i-1,i) is always 0
        pkts = [rtp(seq=i, ts=i * 160) for i in range(5)]
        frames = [(Fraction(i) / 50, ethernet(ipv4(udp(p)))) for i, p in enumerate(pkts)]
        s = parse(pcap(frames))["streams"][0]
        self.assertEqual(s["jitter"], 0.0)

    def test_jitter_value(self):
        # first interval is 30 ms instead of 20 ms -> |D| = 80 timestamp units
        pkts = [rtp(seq=0, ts=0), rtp(seq=1, ts=160), rtp(seq=2, ts=320), rtp(seq=3, ts=480)]
        times = [0, Fraction(30, 1000), Fraction(50, 1000), Fraction(70, 1000)]
        frames = [(t, ethernet(ipv4(udp(p)))) for t, p in zip(times, pkts)]
        s = parse(pcap(frames))["streams"][0]
        self.assertEqual(s["jitter"], 4.39)             # 5 -> 4.6875 -> 4.39453125 -> 4.38232... -> 4.39

    def test_unknown_clock_gives_null(self):
        s = one([rtp(pt=96), rtp(pt=96, seq=2)])
        self.assertIsNone(s["jitter"])

    def test_g722_uses_8khz_clock(self):
        # RFC 3551: G.722 (PT 9) runs on the 8 kHz RTP clock even though the samples are 16 kHz
        pkts = [rtp(pt=9, seq=0, ts=0), rtp(pt=9, seq=1, ts=160)]
        frames = [(0, ethernet(ipv4(udp(pkts[0])))), (Fraction(40, 1000), ethernet(ipv4(udp(pkts[1]))))]
        s = parse(pcap(frames))["streams"][0]
        self.assertEqual(s["jitter"], 10.0)             # (40 ms * 8000 - 160) / 16

    def test_timestamp_wrap_still_zero_jitter(self):
        ts0 = 0xFFFFFFFF - 80
        pkts = [rtp(seq=i, ts=(ts0 + i * 160) & 0xFFFFFFFF) for i in range(3)]
        frames = [(Fraction(i) / 50, ethernet(ipv4(udp(p)))) for i, p in enumerate(pkts)]
        s = parse(pcap(frames))["streams"][0]
        self.assertEqual(s["jitter"], 0.0)


class RtpHeaderTests(unittest.TestCase):
    def test_csrc_excluded(self):
        base = rtp(seq=1, payload=20)
        with_csrc = rtp(seq=2, payload=20,
                        csrc=(struct.pack(">I", 1), struct.pack(">I", 2), struct.pack(">I", 3)))
        s = one([base, with_csrc])
        self.assertEqual(s["payload_bytes"], 40)

    def test_extension_excluded(self):
        base = rtp(seq=1, payload=20)
        with_ext = rtp(seq=2, payload=20, ext=(0xBEDE, b"\xaa" * 4))
        s = one([base, with_ext])
        self.assertEqual(s["payload_bytes"], 40)

    def test_padding_excluded(self):
        base = rtp(seq=1, payload=20)
        padded = rtp(seq=2, payload=20, pad=8)
        s = one([base, padded])
        self.assertEqual(s["payload_bytes"], 40)


class RtcpTests(unittest.TestCase):
    def test_rtcp_filtered(self):
        sr = bytes([0x80, 200, 0x00, 0x06]) + struct.pack(">I", 0xCAFE0001) + bytes(20)   # RTCP SR, PT 200
        s = one([rtp(seq=1), rtp(seq=2), sr])
        self.assertEqual((s["packets"], s["ssrc"]), (2, "cafe0001"))

    def test_all_rtcp_only_gives_no_streams(self):
        rr = bytes([0x80, 201, 0x00, 0x01]) + struct.pack(">I", 0) + bytes(4)
        out = parse(pcap([(1, ethernet(ipv4(udp(rr))))]))
        self.assertEqual(out["streams"], [])


class LinkLayerTests(unittest.TestCase):
    def test_linux_sll(self):
        d = pcap([(1, sll(ipv4(udp(rtp()))))], linktype=113)
        self.assertEqual(parse(d)["link_type"], 113)
        self.assertEqual(len(parse(d)["streams"]), 1)

    def test_linux_sll2(self):
        d = pcap([(1, sll2(ipv4(udp(rtp()))))], linktype=276)
        self.assertEqual(parse(d)["streams"][0]["ssrc"], "cafe0001")

    def test_raw_ipv4(self):
        d = pcap([(1, ipv4(udp(rtp())))], linktype=101)
        self.assertEqual(parse(d)["streams"][0]["ssrc"], "cafe0001")

    def test_raw_ipv6(self):
        d = pcap([(1, ipv6(udp(rtp())))], linktype=101)
        self.assertEqual(parse(d)["streams"][0]["ssrc"], "cafe0001")

    def test_loopback_v4_and_v6(self):
        for lt, fam, ip in ((0, 2, ipv4(udp(rtp()))), (108, 30, ipv6(udp(rtp())))):
            d = pcap([(1, loopback(ip, fam))], linktype=lt)
            self.assertEqual(parse(d)["streams"][0]["ssrc"], "cafe0001")

    def test_ipv6_extension_headers(self):
        exts = [hbh(44), fragment(17)]
        d = pcap([(1, ipv6(udp(rtp()), exts=exts))], linktype=229)
        self.assertEqual(parse(d)["streams"][0]["ssrc"], "cafe0001")

    def test_ipv6_later_fragment_rejected(self):
        d = pcap([(1, ipv6(udp(rtp()), exts=[fragment(17, offset=1)]))], linktype=229)
        self.assertEqual(parse(d)["streams"], [])

    def test_unsupported_linktype(self):
        d = pcap([(1, bytes(40))], linktype=105)
        with self.assertRaises(RtpError):
            parse(d)


class TruncationTests(unittest.TestCase):
    def test_truncated_packet_skipped_and_counted(self):
        frames = [(i + 1, ethernet(ipv4(udp(rtp(seq=i + 1))))) for i in range(3)]
        d = pcap(frames, truncate=(1,))
        out = parse(d)
        self.assertEqual(out["truncated_packets"], 1)
        self.assertEqual(out["streams"][0]["packets"], 2)
        self.assertEqual(out["streams"][0]["duplicates"], 0)


class InvalidRtpTests(unittest.TestCase):
    def test_not_version_two(self):
        self.assertIsNone(rtp_packet(bytes([0x40, 0, 0, 0]) + bytes(8)))

    def test_bad_padding(self):
        bad = bytes([0xA0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])   # padding bit set, pad count 0
        self.assertIsNone(rtp_packet(bad))


if __name__ == "__main__":
    unittest.main()
