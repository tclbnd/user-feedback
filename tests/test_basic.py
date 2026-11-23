import struct
import unittest

from gpmfkit.gpmf import parse

from klv import kl, track


def streams(data, key="ACCL"):
    return parse(data)["devices"][0]["streams"][key]


def device_chunks(*streams):
    """A track whose device is repeated (as a camera writes it once a second)."""
    name = kl("DVNM", "c", b"CAMA", 1, 4)
    return b"".join(kl("DEVC", "\0", name + strm, 1, len(name + strm)) for strm in streams)


class BasicTests(unittest.TestCase):
    def test_device(self):
        dev = parse(track())["devices"][0]
        self.assertEqual((dev["name"], list(dev["streams"])), ("CAMA", ["ACCL"]))

    def test_elements(self):
        st = parse(track(5))["devices"][0]["streams"]["ACCL"]
        self.assertEqual((st["elements"], st["samples"]), (3, 5))


class PaddingTests(unittest.TestCase):
    """Names whose length is not a multiple of four still hide nothing."""

    def test_short_names(self):
        st = streams(track(name=b"HERO9", stnm=b"Accel"))
        self.assertEqual((st["name"], st["samples"]), ("Accel", 4))

    def test_odd_names(self):
        st = streams(track(name=b"HERO10 Black", stnm=b"Accelerometer"))
        self.assertEqual((st["name"], st["samples"]), ("Accelerometer", 4))

    def test_entries_after_short_entry(self):
        # A 5-byte name, then a stream: what follows the name must still be read.
        acc = kl("ACCL", "s", bytes(6 * 2), 6, 2)
        strm = kl("STNM", "c", b"Accel", 1, 5) + acc
        strm = kl("STRM", "\0", strm, 1, len(strm))
        dev = kl("DVNM", "c", b"HERO9", 1, 5) + strm
        st = streams(kl("DEVC", "\0", dev, 1, len(dev)))
        self.assertEqual((st["name"], st["samples"]), ("Accel", 2))


class ScaleTests(unittest.TestCase):
    def test_one_divisor(self):
        payload = kl("ACCL", "s", struct.pack(">3h", 981, -40, 20), 6, 1)
        st = streams(track(payload=payload, scale=100))
        self.assertEqual(st["first"], ["981/100", "-2/5", "1/5"])

    def test_divisor_per_element(self):
        payload = kl("GPS5", "l", struct.pack(">5i", 377749000, -1224194000, 12345, 1532, 1530), 20, 1)
        scale = kl("SCAL", "l", struct.pack(">5i", 10000000, 10000000, 1000, 1000, 100), 20, 1)
        st = streams(track(payload=scale + payload), "GPS5")
        self.assertEqual(st["first"], ["377749/10000", "-612097/5000", "2469/200", "383/250", "153/10"])

    def test_float(self):
        payload = kl("TMPF", "f", struct.pack(">f", 21.5), 4, 1)
        self.assertEqual(streams(track(payload=payload), "TMPF")["first"], ["21.5"])

    def test_fixed_point(self):
        payload = kl("FIXD", "q", struct.pack(">i", 3 << 16), 4, 1)
        self.assertEqual(streams(track(payload=payload), "FIXD")["first"], ["3"])

    def test_text_sample(self):
        payload = kl("TEXT", "c", b"ABCD", 1, 4)
        st = streams(track(payload=payload), "TEXT")
        self.assertEqual((st["first"], st["samples"]), (["ABCD"], 1))


class SampleCountTests(unittest.TestCase):
    def test_whole_track(self):
        # The same stream written three times: the count is their sum.
        sample = kl("ACCL", "s", struct.pack(">3h", 1, 2, 3), 6, 1)
        strm = kl("STRM", "\0", sample, 1, len(sample))
        st = streams(device_chunks(strm, strm, strm))
        self.assertEqual(st["samples"], 3)

    def test_every_chunk_of_a_track(self):
        # Cameras repeat the device every second; each chunk holds its samples.
        payload = kl("ACCL", "s", bytes(6 * 200), 6, 200)
        strm = kl("STRM", "\0", payload, 1, len(payload))
        self.assertEqual(streams(device_chunks(strm, strm, strm))["samples"], 600)

    def test_first_of_track_last_of_track(self):
        first = kl("STRM", "\0", kl("ACCL", "s", struct.pack(">3h", 1, 2, 3), 6, 1), 1, 0)
        last = kl("STRM", "\0", kl("ACCL", "s", struct.pack(">3h", 7, 8, 9), 6, 1), 1, 0)
        first = first[:6] + struct.pack(">H", len(first) - 8) + first[8:]
        last = last[:6] + struct.pack(">H", len(last) - 8) + last[8:]
        st = streams(device_chunks(first, last))
        self.assertEqual((st["first"], st["last"]), (["1", "2", "3"], ["7", "8", "9"]))

    def test_tsmp_last(self):
        payload = kl("TSMP", "L", struct.pack(">I", 10), 4, 1)
        payload += kl("TSMP", "L", struct.pack(">I", 20), 4, 1)
        payload += kl("ACCL", "s", bytes(6), 6, 1)
        self.assertEqual(streams(track(payload=payload))["total_samples"], 20)


class ComplexTypeTests(unittest.TestCase):
    def test_type_entry(self):
        types = kl("TYPE", "c", b"6s", 1, 2)
        payload = kl("ACCL", "?", struct.pack(">6h", 981, -40, 20, 1, 2, 3), 12, 1)
        st = streams(track(payload=types + payload))
        self.assertEqual((st["elements"], st["first"]), (6, ["981", "-40", "20", "1", "2", "3"]))

    def test_gps_scaled_per_element(self):
        types = kl("TYPE", "c", b"lllll", 1, 5)
        raw = struct.pack(">5i", 377749000, -1224194000, 12345, 1532, 1530) * 2
        payload = kl("GPS5", "?", raw, 20, 2)
        scale = kl("SCAL", "l", struct.pack(">5i", 10000000, 10000000, 1000, 1000, 100), 20, 1)
        st = streams(track(payload=types + scale + payload), "GPS5")
        self.assertEqual((st["samples"], st["first"]),
                         (2, ["377749/10000", "-612097/5000", "2469/200", "383/250", "153/10"]))


class StreamTests(unittest.TestCase):
    def test_units_named(self):
        st = streams(track(siun=b"m/s2"))
        self.assertEqual(st["units"], "m/s2")

    def test_orientation(self):
        payload = kl("ORIN", "L", struct.pack(">4I", 0, 1, 0, 1), 16, 1) + kl("ACCL", "s", bytes(6), 6, 1)
        self.assertEqual(streams(track(payload=payload))["orientation"], [0, 1, 0, 1])

    def test_empty(self):
        payload = kl("EMPT", "L", struct.pack(">I", 7), 4, 1) + kl("ACCL", "s", bytes(6), 6, 1)
        self.assertEqual(streams(track(payload=payload))["empty"], 7)

    def test_empty_adds_up(self):
        payload = kl("EMPT", "L", struct.pack(">I", 7), 4, 1) + kl("ACCL", "s", bytes(6), 6, 1)
        strm = kl("STRM", "\0", payload, 1, len(payload))
        self.assertEqual(streams(device_chunks(strm, strm))["empty"], 14)
