import struct
import unittest

from rtpkit.rtp import parse

from caps import (ethernet, ipv4_packet, ipv6_packet, one_packet, pcap,
                  rtp_packet, udp_packet)


def eth_rtp(**kwargs):
    return ethernet(ipv4_packet(udp_packet(rtp_packet(**kwargs))))


def stream(capture):
    return parse(capture)["streams"][0]


class BasicTests(unittest.TestCase):
    def test_one_stream(self):
        out = parse(one_packet())
        self.assertEqual((out["link_type"], len(out["streams"]), out["streams"][0]["ssrc"]), (1, 1, "cafe0001"))

    def test_packets(self):
        s = parse(one_packet())["streams"][0]
        self.assertEqual((s["packets"], s["lost"]), (1, 0))

    def test_not_a_pcap(self):
        with self.assertRaises(ValueError):
            parse(b"not a capture at all")


class CaptureFormatTests(unittest.TestCase):
    def test_big_endian_capture(self):
        s = stream(pcap([eth_rtp(seq=5, payload=b"abcd")], order=">"))
        self.assertEqual((s["packets"], s["payload_bytes"]), (1, 4))

    def test_nanosecond_capture(self):
        out = parse(pcap([eth_rtp(payload=b"abcd")], nanosecond=True, step=500000))
        self.assertTrue(out["nanosecond"])
        self.assertEqual(out["streams"][0]["payload_bytes"], 4)

    def test_nanosecond_big_endian(self):
        out = parse(pcap([eth_rtp(payload=b"abcd")], order=">", nanosecond=True))
        self.assertTrue(out["nanosecond"])
        self.assertEqual(out["streams"][0]["packets"], 1)


class LinkLayerTests(unittest.TestCase):
    def test_vlan_tag(self):
        s = stream(pcap([ethernet(ipv4_packet(udp_packet(rtp_packet(payload=b"abcd"))), vlans=(0x0064,))]))
        self.assertEqual((s["packets"], s["payload_bytes"]), (1, 4))

    def test_stacked_vlan_tags(self):
        s = stream(pcap([ethernet(ipv4_packet(udp_packet(rtp_packet(payload=b"abcd"))), vlans=(1, 2))]))
        self.assertEqual((s["packets"], s["payload_bytes"]), (1, 4))

    def test_linux_cooked(self):
        frame = struct.pack(">HHH", 0, 1, 6) + bytes(8) + struct.pack(">H", 0x0800) + ipv4_packet(udp_packet(rtp_packet()))
        self.assertEqual(stream(pcap([frame], linktype=113))["packets"], 1)

    def test_raw_ip(self):
        self.assertEqual(stream(pcap([ipv4_packet(udp_packet(rtp_packet()))], linktype=101))["packets"], 1)

    def test_bsd_loopback(self):
        frame = struct.pack("<I", 2) + ipv4_packet(udp_packet(rtp_packet()))
        self.assertEqual(stream(pcap([frame], linktype=0))["packets"], 1)

    def test_ipv6(self):
        s = stream(pcap([ethernet(ipv6_packet(udp_packet(rtp_packet(payload=b"abcd"))), ethertype=0x86DD)]))
        self.assertEqual((s["packets"], s["payload_bytes"]), (1, 4))

    def test_rtcp_is_not_a_stream(self):
        rtcp = struct.pack(">BBH", 0x80, 200, 1) + bytes(4)
        udp = struct.pack(">HHHH", 5004, 5004, 8 + len(rtcp), 0) + rtcp
        self.assertEqual(parse(pcap([ethernet(ipv4_packet(udp))]))["streams"], [])


class SequenceTests(unittest.TestCase):
    def test_wraparound(self):
        frames = [eth_rtp(seq=q) for q in (65534, 65535, 0, 2)]
        s = stream(pcap(frames))
        self.assertEqual((s["packets"], s["lost"], s["out_of_order"]), (4, 1, 0))

    def test_wraparound_with_gaps(self):
        # 65500 .. 65535 then 0 .. 20: one 64K cycle, 36 + 33 = 69 packets expected
        seqs = list(range(65500, 65536)) + list(range(0, 21))
        s = stream(pcap([eth_rtp(seq=q) for q in seqs]))
        self.assertEqual((s["packets"], s["lost"], s["out_of_order"]), (len(seqs), 0, 0))

    def test_lost_after_wraparound(self):
        # 65534, 65535, 0, 3: expected 65534..65539, so 65537 and 65538 are lost
        s = stream(pcap([eth_rtp(seq=q) for q in (65534, 65535, 0, 3)]))
        self.assertEqual((s["packets"], s["lost"]), (4, 2))

    def test_duplicates(self):
        s = stream(pcap([eth_rtp(seq=q) for q in (1, 2, 3, 3, 2, 5)]))
        self.assertEqual((s["packets"], s["duplicates"], s["lost"]), (4, 2, 1))

    def test_reordering(self):
        s = stream(pcap([eth_rtp(seq=q) for q in (1, 3, 2, 5)]))
        self.assertEqual((s["packets"], s["duplicates"], s["out_of_order"], s["lost"]), (4, 0, 1, 1))

    def test_reordering_across_wraparound(self):
        # 65535 then 0 then the late 65534: the late packet belongs to the old cycle
        s = stream(pcap([eth_rtp(seq=q) for q in (65535, 0, 65534)]))
        self.assertEqual((s["packets"], s["out_of_order"], s["lost"]), (3, 1, 0))


class PayloadTests(unittest.TestCase):
    def test_csrc_list_excluded(self):
        s = stream(pcap([eth_rtp(pt=9, payload=b"x" * 160, csrc=(1, 2, 3))]))
        self.assertEqual(s["payload_bytes"], 160)

    def test_header_extension_excluded(self):
        s = stream(pcap([eth_rtp(payload=b"x" * 100, ext=bytes(8))]))
        self.assertEqual(s["payload_bytes"], 100)

    def test_padding_excluded(self):
        s = stream(pcap([eth_rtp(payload=b"x" * 100, pad=4)]))
        self.assertEqual(s["payload_bytes"], 100)

    def test_csrc_extension_and_padding_together(self):
        s = stream(pcap([eth_rtp(payload=b"x" * 100, csrc=(1, 2), ext=bytes(4), pad=8)]))
        self.assertEqual(s["payload_bytes"], 100)


class JitterTests(unittest.TestCase):
    def test_g722_clock_rate(self):
        # G.722 (PT 9) uses an 8 kHz RTP clock: 20 ms spacing advances ts by 160.
        frames = [eth_rtp(seq=i, ts=t, pt=9) for i, t in enumerate((0, 160, 320, 480))]
        out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
        for i, frame in enumerate(frames):
            out += struct.pack("<IIII", 0, i * 20000, len(frame), len(frame)) + frame
        s = parse(out)["streams"][0]
        self.assertEqual(s["jitter"], "0.00")

    def test_unknown_payload_type_has_no_jitter(self):
        s = stream(pcap([eth_rtp(pt=96, payload=b"x" * 10)]))
        self.assertIsNone(s["jitter"])


if __name__ == "__main__":
    unittest.main()
