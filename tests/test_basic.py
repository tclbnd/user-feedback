import unittest

from sdpkit.sdp import SdpError, parse

from descriptions import one_stream, session_connection, two_media


class BasicTests(unittest.TestCase):
    def test_media(self):
        m = parse(one_stream())["media"][0]
        self.assertEqual((m["type"], m["port"], m["address"], m["payloads"][0]["encoding"]), ("audio", 5004, "10.0.0.2", "L24"))

    def test_l16_packet(self):
        self.assertEqual(parse(one_stream("L16/48000/2"))["media"][0]["packet_bytes"], 192)

    def test_l24_packet(self):
        # 1 ms of 48 kHz stereo at 3 bytes per sample: 48 * 2 * 3.
        self.assertEqual(parse(one_stream("L24/48000/2"))["media"][0]["packet_bytes"], 288)

    def test_am824_packet(self):
        self.assertEqual(parse(one_stream("AM824/48000/2"))["media"][0]["packet_bytes"], 384)


class SessionLevelTests(unittest.TestCase):
    def test_session_connection_applies(self):
        m = parse(session_connection())["media"][0]
        self.assertEqual((m["address"], m["ttl"]), ("239.69.21.4", 32))

    def test_session_direction_and_refclk_apply(self):
        m = parse(session_connection())["media"][0]
        self.assertEqual(m["direction"], "sendonly")
        self.assertEqual(m["refclk"], "ptp=IEEE1588-2008:00-1D-C1-FF-FE-41-2A-07:0")
        self.assertEqual(m["ptp"], {"version": "IEEE1588-2008", "grandmaster": "00-1D-C1-FF-FE-41-2A-07",
                                    "domain": 0, "traceable": False})

    def test_media_connection_overrides_session(self):
        first, second = parse(two_media())["media"]
        self.assertEqual((first["address"], first["ttl"], first["addresses"]), ("239.69.30.17", 15, 1))
        self.assertEqual((second["address"], second["direction"], second["source"]), ("239.69.30.18", "inactive", "10.0.0.9"))

    def test_media_attributes_default_from_session(self):
        first = parse(two_media())["media"][0]
        self.assertEqual((first["direction"], first["refclk"]), ("recvonly", None))


class ConnectionTests(unittest.TestCase):
    def test_multicast_count(self):
        m = parse(one_stream(connection="IN IP4 239.1.2.3/16/4"))["media"][0]
        self.assertEqual((m["address"], m["ttl"], m["addresses"]), ("239.1.2.3", 16, 4))

    def test_unicast_suffix_is_not_ttl(self):
        m = parse(one_stream(connection="IN IP4 10.0.0.2/32"))["media"][0]
        self.assertEqual((m["address"], m["ttl"], m["addresses"]), ("10.0.0.2", None, 1))

    def test_ipv6_multicast(self):
        m = parse(one_stream(connection="IN IP6 FF1E::1/3"))["media"][0]
        self.assertEqual((m["address"], m["ttl"], m["addresses"]), ("FF1E::1", None, 3))


class TimingTests(unittest.TestCase):
    def test_fractional_ptime(self):
        first = parse(two_media())["media"][0]
        self.assertEqual((first["ptime"], first["packets_per_second"], first["packet_bytes"]), ("1/8", "8000", 36))

    def test_rtp_offset(self):
        self.assertEqual(parse(two_media())["media"][0]["rtp_offset"], 1820480)

    def test_ports(self):
        first, second = parse(two_media())["media"]
        self.assertEqual((first["port"], first["ports"], second["ports"]), (5004, 2, 1))


class PayloadTests(unittest.TestCase):
    def test_mono_channels_default(self):
        m = parse(one_stream("L16/48000", ptime="4"))["media"][0]
        self.assertEqual(m["payloads"][0]["channels"], 1)
        self.assertEqual(m["packet_bytes"], 384)

    def test_static_payload_needs_no_rtpmap(self):
        text = "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0",
                          "m=audio 5004 RTP/AVP 11", "c=IN IP4 10.0.0.2", "a=ptime:1"]) + "\n"
        first = parse(text)["media"][0]["payloads"][0]
        self.assertEqual((first["encoding"], first["clock_rate"], first["channels"]), ("L16", 44100, 1))

    def test_fmtp(self):
        text = one_stream() + "a=fmtp:97 channel-order=SMPTE2110.(ST)\n"
        self.assertEqual(parse(text)["media"][0]["payloads"][0]["fmtp"], {"channel-order": "SMPTE2110.(ST)"})


class RejectionTests(unittest.TestCase):
    def check(self, extra):
        text = "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0",
                          "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000"] + extra) + "\n"
        with self.assertRaises(SdpError):
            parse(text)

    def test_zero_ptime(self):
        self.check(["c=IN IP4 239.1.1.1", "a=ptime:0"])

    def test_non_numeric_ptime(self):
        self.check(["c=IN IP4 239.1.1.1", "a=ptime:soon"])

    def test_ttl_out_of_range(self):
        self.check(["c=IN IP4 239.1.1.1/300"])

    def test_bad_c_line(self):
        self.check(["c=IN IP4 1.2.3.4 5"])

    def test_bad_rtpmap(self):
        text = "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0",
                          "m=audio 5004 RTP/AVP 97", "c=IN IP4 10.0.0.2", "a=rtpmap:97 bogus"]) + "\n"
        with self.assertRaises(SdpError):
            parse(text)


class ReferenceClockTests(unittest.TestCase):
    def test_traceable(self):
        m = parse(one_stream() + "a=ts-refclk:ptp=IEEE1588-2008:traceable\n")["media"][0]
        self.assertEqual(m["ptp"], {"version": "IEEE1588-2008", "grandmaster": None,
                                    "domain": None, "traceable": True})

    def test_no_ptp(self):
        m = parse(one_stream() + "a=ts-refclk:localmac=00-1D-C1-41-2A-07\n")["media"][0]
        self.assertIsNone(m["ptp"])
        self.assertEqual(m["refclk"], "localmac=00-1D-C1-41-2A-07")

    def test_bandwidth_and_session_source(self):
        text = "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0",
                          "a=source-filter:incl IN IP4 239.1.1.1 10.0.0.7",
                          "m=audio 5004 RTP/AVP 97", "c=IN IP4 239.1.1.1", "a=rtpmap:97 L24/48000",
                          "a=ptime:1", "b=AS:12000"]) + "\n"
        m = parse(text)["media"][0]
        self.assertEqual((m["bandwidth_kbps"], m["source"]), (12000, "10.0.0.7"))


