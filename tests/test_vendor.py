import unittest

from sdpkit.sdp import SdpError, connection, parse

from descriptions import one_stream


def session(lines):
    return "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=S", "t=0 0"] + lines) + "\n"


class ConnectionTests(unittest.TestCase):
    def test_spurious_cidr_tail_has_no_ttl(self):
        # The "/32" vendors append to multicast addresses is a routing
        # prefix, not the RFC 8866 TTL.
        self.assertEqual(connection("IN IP4 239.69.21.4/32"),
                         {"address": "239.69.21.4", "ttl": None, "addresses": 1})

    def test_real_ttl_is_kept(self):
        self.assertEqual(connection("IN IP4 239.69.30.17/15"),
                         {"address": "239.69.30.17", "ttl": 15, "addresses": 1})

    def test_ttl_and_address_count(self):
        self.assertEqual(connection("IN IP4 239.0.0.1/15/3"),
                         {"address": "239.0.0.1", "ttl": 15, "addresses": 3})

    def test_ipv6_has_no_ttl(self):
        self.assertEqual(connection("IN IP6 ff08::1"),
                         {"address": "ff08::1", "ttl": None, "addresses": 1})
        self.assertEqual(connection("IN IP6 ff08::1/3"),
                         {"address": "ff08::1", "ttl": None, "addresses": 3})
        self.assertEqual(connection("IN IP6 ff08::1/128"),
                         {"address": "ff08::1", "ttl": None, "addresses": 1})


class InheritanceTests(unittest.TestCase):
    def test_session_connection_applies_to_media(self):
        text = session(["c=IN IP4 239.69.21.4/32",
                        "m=audio 5004 RTP/AVP 98", "a=rtpmap:98 L24/48000/8",
                        "a=ptime:1"])
        media = parse(text)["media"][0]
        self.assertEqual((media["address"], media["ttl"], media["addresses"]),
                         ("239.69.21.4", None, 1))

    def test_media_connection_overrides_session(self):
        text = session(["c=IN IP4 239.0.0.1/15",
                        "m=audio 5004 RTP/AVP 98", "c=IN IP4 239.0.0.2/64",
                        "a=rtpmap:98 L24/48000/8", "a=ptime:1"])
        media = parse(text)["media"][0]
        self.assertEqual((media["address"], media["ttl"]), ("239.0.0.2", 64))

    def test_direction_and_refclk_fall_back_to_session(self):
        text = session(["a=sendonly",
                        "a=ts-refclk:ptp=IEEE1588-2008:00-1d-c1-ff-fe-41-2a-07:0",
                        "m=audio 5004 RTP/AVP 98", "a=rtpmap:98 L24/48000/8",
                        "a=ptime:1"])
        media = parse(text)["media"][0]
        self.assertEqual(media["direction"], "sendonly")
        self.assertEqual(media["refclk"],
                         "ptp=IEEE1588-2008:00-1d-c1-ff-fe-41-2a-07:0")

    def test_media_direction_overrides_session(self):
        text = session(["a=sendonly", "m=audio 5004 RTP/AVP 97", "a=recvonly",
                        "a=rtpmap:97 L24/48000/1", "a=ptime:1"])
        self.assertEqual(parse(text)["media"][0]["direction"], "recvonly")


class PayloadTests(unittest.TestCase):
    def test_missing_channels_default_to_mono(self):
        media = parse(one_stream("L16/48000", "4"))["media"][0]
        self.assertEqual(media["payloads"][0]["channels"], 1)
        self.assertEqual(media["packet_bytes"], 384)

    def test_fractional_ptime(self):
        media = parse(one_stream("L24/48000/2", "0.125"))["media"][0]
        self.assertEqual(media["ptime"], "1/8")
        self.assertEqual(media["packets_per_second"], "8000")
        self.assertEqual(media["packet_bytes"], 36)

    def test_l24_is_three_bytes_per_sample(self):
        media = parse(one_stream("L24/48000/8", "1"))["media"][0]
        self.assertEqual(media["packet_bytes"], 1152)

    def test_am824_sizing(self):
        text = session(["m=audio 5004 RTP/AVP 97", "a=rtpmap:97 AM824/48000/2",
                        "a=ptime:1"])
        media = parse(text)["media"][0]
        self.assertEqual(media["packet_bytes"], 288)

    def test_static_payload_type_needs_no_rtpmap(self):
        text = session(["m=audio 5004 RTP/AVP 0", "a=ptime:20"])
        payload = parse(text)["media"][0]["payloads"][0]
        self.assertEqual((payload["encoding"], payload["clock_rate"],
                          payload["channels"]), ("PCMU", 8000, 1))

    def test_port_count(self):
        text = session(["m=audio 5004/2 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        media = parse(text)["media"][0]
        self.assertEqual((media["port"], media["ports"]), (5004, 2))

    def test_fractional_ptime_with_whole_samples_required(self):
        with self.assertRaises(SdpError):
            parse(one_stream("L24/48000/1", "0.1"))


class PtpTests(unittest.TestCase):
    def test_ptp_with_grandmaster_and_domain(self):
        text = session(["a=ts-refclk:ptp=IEEE1588-2008:00-1d-c1-ff-fe-41-2a-07:7",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertEqual(parse(text)["media"][0]["ptp"],
                         {"version": "IEEE1588-2008",
                          "grandmaster": "00-1D-C1-FF-FE-41-2A-07",
                          "domain": 7, "traceable": False})

    def test_ptp_domain_defaults_to_zero(self):
        text = session(["a=ts-refclk:ptp=IEEE1588-2008:00-1D-C1-FF-FE-41-2A-07",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertEqual(parse(text)["media"][0]["ptp"]["domain"], 0)

    def test_ptp_traceable(self):
        text = session(["a=ts-refclk:ptp=IEEE1588-2008:traceable",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertEqual(parse(text)["media"][0]["ptp"],
                         {"version": "IEEE1588-2008", "grandmaster": None,
                          "domain": None, "traceable": True})

    def test_other_clock_is_null(self):
        text = session(["a=ts-refclk:localmac=01-02-03-04-05-06",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertIsNone(parse(text)["media"][0]["ptp"])


class MediaClockAndSourceTests(unittest.TestCase):
    def test_rtp_offset(self):
        text = session(["m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1", "a=mediaclk:direct=1820480"])
        self.assertEqual(parse(text)["media"][0]["rtp_offset"], 1820480)

    def test_no_mediaclk_offset(self):
        text = session(["m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertIsNone(parse(text)["media"][0]["rtp_offset"])

    def test_source_filter_falls_back_to_session(self):
        text = session(["a=source-filter: incl IN IP4 239.1.1.1 10.2.3.4",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertEqual(parse(text)["media"][0]["source"], "10.2.3.4")

    def test_source_filter_media_first_address(self):
        text = session(["a=source-filter: incl IN IP4 239.1.1.1 10.2.3.4",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1",
                        "a=source-filter: incl IN IP4 239.1.1.1 10.9.9.9 10.9.9.10"])
        self.assertEqual(parse(text)["media"][0]["source"], "10.9.9.9")

    def test_exclusive_filter_has_no_source(self):
        text = session(["a=source-filter: excl IN IP4 239.1.1.1 10.2.3.4",
                        "m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/1",
                        "a=ptime:1"])
        self.assertIsNone(parse(text)["media"][0]["source"])


class OrderedChannelsTests(unittest.TestCase):
    def _media(self, order):
        lines = ["m=audio 5004 RTP/AVP 97", "a=rtpmap:97 L24/48000/8",
                 "a=ptime:1"]
        if order is not None:
            lines.append("a=fmtp:97 channel-order=" + order)
        return parse(session(lines))["media"][0]

    def test_single_and_group_symbols(self):
        self.assertEqual(self._media("SMPTE2110.(ST)")["ordered_channels"], 2)
        self.assertEqual(self._media("SMPTE2110.(L,R,C,LFE,Lss,Rss)")["ordered_channels"], 6)
        self.assertEqual(self._media("SMPTE2110.(51)")["ordered_channels"], 6)
        self.assertEqual(self._media("SMPTE2110.(71)")["ordered_channels"], 8)
        self.assertEqual(self._media("SMPTE2110.(222)")["ordered_channels"], 24)

    def test_unknown_convention_or_symbol(self):
        self.assertIsNone(self._media("EBU.(ST)")["ordered_channels"])
        self.assertIsNone(self._media("SMPTE2110.(XY)")["ordered_channels"])
        self.assertIsNone(self._media(None)["ordered_channels"])


class ExampleFilesTests(unittest.TestCase):
    def test_stagebox_and_console_parse(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("console_out.sdp", "stagebox.sdp"):
            with open(os.path.join(here, "examples", name), encoding="utf-8") as fh:
                result = parse(fh.read())
            self.assertNotIn("error", result)
            self.assertTrue(result["media"])


if __name__ == "__main__":
    unittest.main()
