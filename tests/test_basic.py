import os
import struct
import unittest

from gpmfkit.gpmf import parse

from klv import container, devc, kl, strm, track

EXAMPLES = os.path.join(os.path.dirname(__file__), os.pardir, "examples")


def accel(*samples):
    raw = b"".join(struct.pack(">3h", *s) for s in samples)
    return kl("ACCL", "s", raw, 6, len(samples))


def gps(*samples):
    raw = b"".join(struct.pack(">5i", *s) for s in samples)
    return kl("GPS5", "l", raw, 20, len(samples))


def parsed(data):
    return parse(data)["devices"][0]


class BasicTests(unittest.TestCase):
    def test_device(self):
        dev = parsed(track())
        self.assertEqual((dev["id"], dev["name"], list(dev["streams"])), ("1", "CAMA", ["ACCL"]))

    def test_elements(self):
        st = parsed(track(5))["streams"]["ACCL"]
        self.assertEqual((st["elements"], st["samples"]), (3, 5))

    def test_name_length_not_multiple_of_four(self):
        # A five-character DVNM must not shift the entries that follow it.
        dev = parsed(devc(strm(accel((1, 2, 3))), name="HERO9"))
        self.assertEqual(dev["name"], "HERO9")
        self.assertEqual(dev["streams"]["ACCL"]["samples"], 1)

    def test_padding_between_entries(self):
        # STNM is 13 bytes, SIUN 4, SCAL 2: three paddings in a row.
        data = devc(strm(kl("STNM", "c", b"Accelerometer"), kl("SIUN", "c", b"m/s2"),
                         kl("SCAL", "s", struct.pack(">h", 100)), accel((981, -12, 35))))
        st = parsed(data)["streams"]["ACCL"]
        self.assertEqual((st["name"], st["units"], st["first"]), ("Accelerometer", "m/s2", ["981/100", "-3/25", "7/20"]))

    def test_stream_name_and_units_whole_text(self):
        data = devc(strm(kl("STNM", "c", b"GPS (Lat., Long., Alt., 2D speed, 3D speed)"),
                         kl("UNIT", "c", b"degdegm m/sm/s"), gps((1, 2, 3, 4, 5))))
        st = parsed(data)["streams"]["GPS5"]
        self.assertEqual((st["name"], st["units"]), ("GPS (Lat., Long., Alt., 2D speed, 3D speed)",
                                                     "degdegm m/sm/s"))

    def test_big_endian_values(self):
        data = devc(strm(kl("SCAL", "s", struct.pack(">h", 100)), accel((981, -12, 35))))
        self.assertEqual(parsed(data)["streams"]["ACCL"]["first"], ["981/100", "-3/25", "7/20"])

    def test_scal_per_element(self):
        scal = kl("SCAL", "l", struct.pack(">5i", 10000000, 10000000, 1000, 1000, 100))
        data = devc(strm(scal, gps((100, -200, 3000, 500, 600))))
        self.assertEqual(parsed(data)["streams"]["GPS5"]["first"],
                         ["1/100000", "-1/50000", "3", "1/2", "6"])

    def test_single_scal_applies_to_all_elements(self):
        data = devc(strm(kl("SCAL", "s", struct.pack(">h", 2)), accel((1, 2, 3))))
        self.assertEqual(parsed(data)["streams"]["ACCL"]["first"], ["1/2", "1", "3/2"])

    def test_samples_over_whole_track(self):
        # Four one-second DEVC blocks of 200 samples each.
        blocks = [strm(kl("TSMP", "L", struct.pack(">I", 200 * (i + 1))), accel(*([(1, 2, 3)] * 200)))
                  for i in range(4)]
        dev = parsed(container("DEVC", kl("DVNM", "c", b"CAMA") + b"".join(blocks)))
        st = dev["streams"]["ACCL"]
        self.assertEqual((st["samples"], st["total_samples"]), (800, 800))

    def test_last_across_track(self):
        blocks = [strm(accel((1, 2, 3))), strm(accel((4, 5, 6)))]
        st = parsed(container("DEVC", b"".join(blocks)))["streams"]["ACCL"]
        self.assertEqual((st["samples"], st["first"], st["last"]), (2, ["1", "2", "3"], ["4", "5", "6"]))

    def test_last_uses_its_own_scal(self):
        blocks = [strm(kl("SCAL", "s", struct.pack(">h", 10)), accel((10, 20, 30))),
                  strm(kl("SCAL", "s", struct.pack(">h", 100)), accel((100, 200, 300)))]
        st = parsed(container("DEVC", b"".join(blocks)))["streams"]["ACCL"]
        self.assertEqual((st["first"], st["last"]), (["1", "2", "3"], ["1", "2", "3"]))

    def test_float_format(self):
        # size is the bytes of one sample, repeat the samples in the payload.
        data = devc(strm(kl("SCAL", "f", struct.pack(">f", 2.0)),
                         kl("TMPC", "f", struct.pack(">2f", 21.5, 22.25), 8, 1)))
        self.assertEqual(parsed(data)["streams"]["TMPC"]["first"], ["10.75", "11.125"])

    def test_fixed_point(self):
        # q is a 16.16 fixed-point number, not a float.
        data = devc(strm(kl("TMPC", "q", struct.pack(">i", 0x00018000), 4, 1)))
        self.assertEqual(parsed(data)["streams"]["TMPC"]["first"], ["3/2"])

    def test_complex_samples_use_type_entry(self):
        # TYPE gives the element types of one sample: q (16.16 fixed) then s.
        raw = struct.pack(">i", 2 << 16) + struct.pack(">h", 4)
        data = devc(strm(kl("TYPE", "c", b"qs"), kl("GPS9", "?", raw, 6, 1)))
        st = parsed(data)["streams"]["GPS9"]
        self.assertEqual((st["elements"], st["first"]), (2, ["2", "4"]))

    def test_orientation(self):
        data = devc(strm(kl("ORIN", "c", b"XYZ"), accel((1, 2, 3))))
        self.assertEqual(parsed(data)["streams"]["ACCL"]["orientation"], "XYZ")

    def test_gps_fix_and_precision(self):
        data = devc(strm(gps((1, 2, 3, 4, 5)), kl("GPSF", "L", struct.pack(">I", 3)),
                         kl("GPSP", "L", struct.pack(">I", 12345))))
        st = parsed(data)["streams"]["GPS5"]
        self.assertEqual((st["gps_fix"], st["gps_precision"]), ("3d", "2469/20"))

    def test_gps_fix_three_d_last_payload_wins(self):
        data = devc(strm(gps((1, 2, 3, 4, 5), (6, 7, 8, 9, 10)), kl("GPSF", "L", struct.pack(">I", 2))),
                    strm(gps((11, 12, 13, 14, 15)), kl("GPSF", "L", struct.pack(">I", 0))))
        st = parsed(data)["streams"]["GPS5"]
        self.assertEqual((st["gps_fix"], st["last"]), ("none", ["11", "12", "13", "14", "15"]))

    def test_empty_counts_add_up(self):
        # EMPT is a header-only entry whose repeat field is the empty count.
        empty = lambda n: kl("EMPT", "\0", b"", 1, n)
        data = devc(strm(gps((1, 2, 3, 4, 5)), empty(2), gps((1, 2, 3, 4, 5)), empty(3)))
        st = parsed(data)["streams"]["GPS5"]
        self.assertEqual((st["samples"], st["empty"]), (2, 5))

    def test_no_tsmp_gives_null_total(self):
        self.assertIsNone(parsed(track())["streams"]["ACCL"]["total_samples"])


class ExampleTests(unittest.TestCase):
    """The two full tracks: HERO9 (12-byte DVNM) and HERO10 (12-byte DVNM, 197 samples)."""

    def load(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return parse(fh.read())

    def test_hero9(self):
        dev = self.load("GX010042.bin")["devices"][0]
        self.assertEqual((dev["id"], dev["name"]), ("1", "HERO9 Black"))
        acc, gyro, gps_ = (dev["streams"][k] for k in ("ACCL", "GYRO", "GPS5"))
        self.assertEqual((acc["name"], acc["units"], acc["elements"], acc["samples"], acc["first"]),
                         ("Accelerometer", "m/s2", 3, 800, ["981/100", "-3/25", "7/20"]))
        self.assertEqual((gyro["name"], gyro["units"], gyro["samples"], gyro["first"]),
                         ("Gyroscope", "rad/s", 800, ["5/939", "-7/939", "1/313"]))
        self.assertEqual((gps_["name"], gps_["elements"], gps_["samples"], gps_["first"]),
                         ("GPS (Lat., Long., Alt., 2D speed, 3D speed)", 5, 72,
                          ["377749/10000", "-612097/5000", "2469/200", "383/250", "153/10"]))

    def test_hero10(self):
        dev = self.load("GX010043.bin")["devices"][0]
        self.assertEqual((dev["id"], dev["name"]), ("1", "HERO10 Black"))
        acc, gps_ = dev["streams"]["ACCL"], dev["streams"]["GPS5"]
        self.assertEqual((acc["samples"], acc["total_samples"]), (591, 591))
        self.assertEqual(gps_["samples"], 30)
        self.assertNotIn("GYRO", dev["streams"])

