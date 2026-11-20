import os
import unittest

from spsinfo.annexb import nal_units
from spsinfo.sps import parse, SpsError, _remove_emulation_prevention

_EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def _first_sps(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return next(n for n in nal_units(data) if n and n[0] & 0x1F == 7)


class BitWriter:
    def __init__(self):
        self.bits = ""

    def u(self, value, n):
        self.bits += format(value, "0%db" % n)

    def flag(self, value):
        self.bits += "1" if value else "0"

    def ue(self, value):
        code = value + 1
        z = code.bit_length() - 1
        self.bits += "0" * z + bin(code)[2:]

    def se(self, value):
        code_num = -2 * value if value <= 0 else 2 * value - 1
        self.ue(code_num)

    def rbsp_trailing(self):
        self.bits += "1"
        while len(self.bits) % 8:
            self.bits += "0"

    def bytes(self):
        return bytes(int(self.bits[i:i + 8], 2) for i in range(0, len(self.bits), 8))


def add_emulation_prevention(rbsp):
    out = bytearray()
    zeros = 0
    for b in rbsp:
        if zeros >= 2 and b <= 0x03:
            out.append(0x03)
            zeros = 0
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def nal(sps_rbsp):
    # forbidden_zero_bit(1) nal_ref_idc(3) nal_unit_type=7(5)
    return b"\x67" + add_emulation_prevention(sps_rbsp)


def scaling_list(w, deltas, size):
    # flat delta sequence terminated by next_scale reaching 0, else full list
    last = 8
    nxt = 8
    emitted = 0
    for ds in deltas:
        w.se(ds)
        nxt = (last + ds + 256) % 256
        if nxt != 0:
            last = nxt
        emitted += 1
        if nxt == 0:
            return
    # pad remaining entries with delta 0 (next_scale unchanged) if caller wants
    for _ in range(size - emitted):
        w.se(0)


def build_sps(profile=66, level=30, constraints=0, *,
             chroma=1, separate=False, depth_l=8, depth_c=8,
             scaling_present=False, scaling_lists=None,
             frame_num_minus4=0, poc_type=0, poc_lsb_minus4=0,
             poc1=None, max_ref=1, gaps=False,
             w_mbs_minus1=0, h_map_minus1=0, frame_mbs_only=True,
             mb_adaptive=False, direct_8x8=True,
             crop=(0, 0, 0, 0), vui=None):
    w = BitWriter()
    w.u(profile, 8)
    w.u(constraints, 8)
    w.u(level, 8)
    w.ue(0)  # sps_id
    high = profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135)
    if high:
        w.ue(chroma)
        if chroma == 3:
            w.flag(separate)
        w.ue(depth_l - 8)
        w.ue(depth_c - 8)
        w.flag(False)  # qpprime
        w.flag(scaling_present)
        if scaling_present:
            n_lists = 12 if (separate and chroma == 3) else 8
            for i in range(n_lists):
                present = bool(scaling_lists and i in scaling_lists)
                w.flag(present)
                if present:
                    size = 16 if i < 6 else 64
                    scaling_list(w, scaling_lists[i], size)
    w.ue(frame_num_minus4)
    w.ue(poc_type)
    if poc_type == 0:
        w.ue(poc_lsb_minus4)
    elif poc_type == 1:
        d = poc1 or {}
        w.flag(d.get("always_zero", False))
        w.se(d.get("offset_non_ref", 0))
        w.se(d.get("offset_top_bottom", 0))
        refs = d.get("ref_offsets", [])
        w.ue(len(refs))
        for v in refs:
            w.se(v)
    w.ue(max_ref)
    w.flag(gaps)
    w.ue(w_mbs_minus1)
    w.ue(h_map_minus1)
    w.flag(frame_mbs_only)
    if not frame_mbs_only:
        w.flag(mb_adaptive)
    w.flag(direct_8x8)
    L, R, T, B = crop
    has_crop = any(crop)
    w.flag(has_crop)
    if has_crop:
        w.ue(L); w.ue(R); w.ue(T); w.ue(B)
    w.flag(vui is not None)
    if vui is not None:
        build_vui(w, vui)
    w.rbsp_trailing()
    return w.bytes()


def build_vui(w, v):
    if "sar_idc" in v:
        w.flag(True)
        w.u(v["sar_idc"], 8)
        if v["sar_idc"] == 255:
            w.u(v["sar_w"], 16)
            w.u(v["sar_h"], 16)
    else:
        w.flag(False)
    w.flag(False)  # overscan
    if "full_range" in v or "video_format" in v:
        w.flag(True)
        w.u(v.get("video_format", 5), 3)
        w.flag(v.get("full_range", False))
        w.flag(False)  # colour description
    else:
        w.flag(False)
    w.flag(False)  # chroma loc
    if "timing" in v:
        w.flag(True)
        units, scale = v["timing"]
        w.u(units, 32)
        w.u(scale, 32)
        w.flag(v.get("fixed", True))
    else:
        w.flag(False)
    for _ in range(5):  # nal hrd, vcl hrd, low delay, pic struct, bit restrict
        w.flag(False)


class BaselineTests(unittest.TestCase):
    def test_simple_baseline_dimensions(self):
        # 1280x720 progressive, no crop: w_mbs=80, h_map=45
        rbsp = build_sps(profile=66, level=31, w_mbs_minus1=79, h_map_minus1=44)
        info = parse(nal(rbsp))
        self.assertEqual(info["profile_idc"], 66)
        self.assertEqual(info["width"], 1280)
        self.assertEqual(info["height"], 720)
        self.assertTrue(info["frame_mbs_only"])
        self.assertEqual(info["chroma_format_idc"], 1)
        self.assertEqual(info["bit_depth_luma"], 8)

    def test_baseline_has_no_high_extension(self):
        rbsp = build_sps(profile=66, w_mbs_minus1=0, h_map_minus1=0)
        info = parse(nal(rbsp))
        self.assertEqual((info["bit_depth_luma"], info["bit_depth_chroma"]), (8, 8))


class HighProfileTests(unittest.TestCase):
    def test_high_422_10bit(self):
        rbsp = build_sps(profile=122, level=41, chroma=2,
                         depth_l=10, depth_c=10,
                         w_mbs_minus1=119, h_map_minus1=67,
                         frame_mbs_only=False, crop=(0, 0, 0, 4))
        info = parse(nal(rbsp))
        self.assertEqual(info["profile_idc"], 122)
        self.assertEqual(info["chroma_format_idc"], 2)
        self.assertEqual(info["bit_depth_luma"], 10)
        self.assertEqual(info["bit_depth_chroma"], 10)
        self.assertFalse(info["frame_mbs_only"])

    def test_high_444_separate_colour_plane(self):
        rbsp = build_sps(profile=244, level=50, chroma=3, separate=True,
                         depth_l=12, depth_c=12, w_mbs_minus1=0, h_map_minus1=0)
        info = parse(nal(rbsp))
        self.assertEqual(info["chroma_format_idc"], 3)
        self.assertEqual(info["bit_depth_luma"], 12)
        self.assertEqual(info["width"], 16)
        self.assertEqual(info["height"], 16)

    def test_all_high_profiles_have_extension(self):
        for prof in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            rbsp = build_sps(profile=prof, level=30, chroma=1,
                             w_mbs_minus1=0, h_map_minus1=0)
            info = parse(nal(rbsp))
            self.assertEqual(info["profile_idc"], prof)


class ScalingMatrixTests(unittest.TestCase):
    def test_default_scaling_matrices(self):
        rbsp = build_sps(profile=100, scaling_present=True,
                         scaling_lists={}, w_mbs_minus1=10, h_map_minus1=10)
        info = parse(nal(rbsp))
        self.assertEqual(info["width"], 176)

    def test_custom_scaling_list_keeps_alignment(self):
        # a few deltas that never zero out, then full list
        rbsp = build_sps(profile=100, scaling_present=True,
                         scaling_lists={0: [3, -1, 2] + [0] * 13},
                         w_mbs_minus1=119, h_map_minus1=67)
        info = parse(nal(rbsp))
        self.assertEqual(info["width"], 1920)
        self.assertEqual(info["height"], 1088)

    def test_early_terminated_scaling_list(self):
        # delta sequence that drives next_scale back to zero
        rbsp = build_sps(profile=100, scaling_present=True,
                         scaling_lists={0: [2, -2]},  # 8 -> 10 -> 0
                         w_mbs_minus1=119, h_map_minus1=67)
        info = parse(nal(rbsp))
        self.assertEqual(info["width"], 1920)


class PocTests(unittest.TestCase):
    def test_poc_type_0(self):
        rbsp = build_sps(profile=66, poc_type=0, poc_lsb_minus4=4,
                         w_mbs_minus1=5, h_map_minus1=5)
        self.assertEqual(parse(nal(rbsp))["width"], 96)

    def test_poc_type_1_with_ref_offsets(self):
        rbsp = build_sps(profile=66, poc_type=1,
                         poc1={"always_zero": True, "offset_non_ref": -2,
                               "offset_top_bottom": 1, "ref_offsets": [3, -1, 0]},
                         w_mbs_minus1=5, h_map_minus1=5)
        self.assertEqual(parse(nal(rbsp))["width"], 96)

    def test_poc_type_2(self):
        rbsp = build_sps(profile=66, poc_type=2,
                         w_mbs_minus1=5, h_map_minus1=5)
        self.assertEqual(parse(nal(rbsp))["width"], 96)

    def test_invalid_poc_type_rejected(self):
        w = BitWriter()
        w.u(66, 8); w.u(0, 8); w.u(30, 8); w.ue(0)
        w.ue(0); w.ue(7)  # bad poc type
        w.rbsp_trailing()
        with self.assertRaises(SpsError):
            parse(nal(w.bytes()))


class InterlaceAndCropTests(unittest.TestCase):
    def test_interlaced_frame_height_doubles(self):
        # h_map=33, interlaced -> 68 MBs tall = 1088; crop B=4 (4:2:2 SubH=1)
        rbsp = build_sps(profile=122, chroma=2, depth_l=10, depth_c=10,
                         frame_mbs_only=False,
                         w_mbs_minus1=119, h_map_minus1=33,
                         crop=(0, 0, 0, 4))
        info = parse(nal(rbsp))
        self.assertFalse(info["frame_mbs_only"])
        self.assertEqual((info["width"], info["height"]), (1920, 1080))

    def test_progressive_420_crop_units(self):
        # SubWidth=SubHeight=2: horizontal crop unit 4 px, vertical 4 px
        rbsp = build_sps(profile=100, chroma=1, frame_mbs_only=True,
                         w_mbs_minus1=119, h_map_minus1=67,
                         crop=(0, 0, 0, 4))
        info = parse(nal(rbsp))
        self.assertEqual(info["height"], 1088 - 4 * 4)

    def test_422_vertical_crop_unit_is_two(self):
        # 4:2:2 SubHeight=1 -> vertical crop unit 2 px
        rbsp = build_sps(profile=122, chroma=2, frame_mbs_only=True,
                         w_mbs_minus1=119, h_map_minus1=67,
                         crop=(0, 0, 0, 4))
        info = parse(nal(rbsp))
        self.assertEqual(info["height"], 1088 - 4 * 2)

    def test_444_crop_units_are_one(self):
        rbsp = build_sps(profile=244, chroma=3, frame_mbs_only=True,
                         w_mbs_minus1=10, h_map_minus1=10,
                         crop=(2, 2, 2, 2))
        info = parse(nal(rbsp))
        self.assertEqual(info["width"], 11 * 16 - 4 * 2)
        self.assertEqual(info["height"], 11 * 16 - 4 * 2)


class LevelTests(unittest.TestCase):
    def test_regular_levels(self):
        for level_idc, name in ((30, "3"), (31, "3.1"), (40, "4"), (51, "5.1")):
            rbsp = build_sps(profile=66, level=level_idc)
            self.assertEqual(parse(nal(rbsp))["level"], name)

    def test_level_1b_via_idc_11(self):
        rbsp = build_sps(profile=66, level=11)
        self.assertEqual(parse(nal(rbsp))["level"], "1b")

    def test_level_1b_via_constraint_flag(self):
        rbsp = build_sps(profile=66, level=9, constraints=0x10)
        self.assertEqual(parse(nal(rbsp))["level"], "1b")


class SarTests(unittest.TestCase):
    def test_known_sar(self):
        rbsp = build_sps(profile=66, vui={"sar_idc": 14})
        self.assertEqual(parse(nal(rbsp))["sar"], "4:3")

    def test_extended_sar(self):
        rbsp = build_sps(profile=66, vui={"sar_idc": 255, "sar_w": 123, "sar_h": 100})
        self.assertEqual(parse(nal(rbsp))["sar"], "123:100")

    def test_unspecified_sar(self):
        rbsp = build_sps(profile=66, vui={"sar_idc": 0})
        self.assertEqual(parse(nal(rbsp))["sar"], "unknown")


class RangeAndTimingTests(unittest.TestCase):
    def test_full_range_flag(self):
        rbsp = build_sps(profile=66, vui={"full_range": True})
        self.assertTrue(parse(nal(rbsp))["full_range"])

    def test_limited_range_default(self):
        rbsp = build_sps(profile=66)
        self.assertFalse(parse(nal(rbsp))["full_range"])

    def test_frame_rate_uses_factor_two(self):
        rbsp = build_sps(profile=66, vui={"timing": (1, 60), "fixed": True})
        self.assertEqual(parse(nal(rbsp))["fps"], "30")

    def test_ntsc_frame_rate_fraction(self):
        rbsp = build_sps(profile=66, vui={"timing": (1001, 60000), "fixed": True})
        self.assertEqual(parse(nal(rbsp))["fps"], "30000/1001")


class EmulationPreventionTests(unittest.TestCase):
    def test_prevention_bytes_removed_before_fields(self):
        # Splice raw 00 00 00 / 01 patterns that are forbidden in an EBSP and
        # confirm the prevention bytes are stripped before bit reading.
        payload = b"\x64\x00\x1f" + b"\x00\x00\x03\x00" + b"\x00\x00\x03\x01"
        self.assertIn(b"\x00\x00\x03", payload)
        clean = _remove_emulation_prevention(payload[1:])
        self.assertEqual(clean, b"\x00\x1f\x00\x00\x00\x00\x00\x01")

    def test_epb_preserved_dimensions_on_real_pattern(self):
        # Hand-built RBSP; parsing must round trip even if the EBSP encoder
        # happens to insert prevention bytes.
        w = BitWriter()
        w.u(100, 8); w.u(0, 8); w.u(31, 8)  # High profile, level 3.1
        w.ue(0)          # sps id
        w.ue(1)          # chroma 4:2:0
        w.ue(0); w.ue(0)  # 8-bit luma/chroma
        w.flag(False)    # qpprime
        w.flag(False)    # no scaling matrix
        w.ue(0)          # frame_num minus4
        w.ue(2)          # poc type 2 (no extra fields)
        w.ue(1)          # max ref frames
        w.flag(False)    # gaps
        w.ue(119)        # 1920 wide
        w.ue(67)         # 1088 tall
        w.flag(True)     # frame_mbs_only
        w.flag(True)     # direct 8x8
        w.flag(False)    # no cropping
        w.flag(False)    # no VUI
        w.rbsp_trailing()
        ebsp = add_emulation_prevention(w.bytes())
        info = parse(b"\x67" + ebsp)
        self.assertEqual(info["width"], 1920)
        self.assertEqual(info["height"], 1088)

    def test_unescape_helper(self):
        self.assertEqual(_remove_emulation_prevention(b"\x00\x00\x03\x00\x01"),
                         b"\x00\x00\x00\x01")
        self.assertEqual(_remove_emulation_prevention(b"\x00\x00\x03\x03"),
                         b"\x00\x00\x03")


class ExampleStreamTests(unittest.TestCase):
    """End-to-end regression on the shipped, hand-authored streams."""

    def test_broadcast_1080i_high422_10bit(self):
        info = parse(_first_sps(os.path.join(_EXAMPLES, "broadcast_1080i.h264")))
        self.assertEqual(info["profile_idc"], 122)
        self.assertEqual(info["chroma_format_idc"], 2)
        self.assertEqual(info["bit_depth_luma"], 10)
        self.assertEqual(info["bit_depth_chroma"], 10)
        self.assertFalse(info["frame_mbs_only"])  # interlaced
        self.assertEqual((info["width"], info["height"]), (1920, 1080))
        self.assertEqual(info["level"], "4.1")
        self.assertEqual(info["fps"], "30000/1001")

    def test_phone_1080p_with_emulation_prevention(self):
        path = os.path.join(_EXAMPLES, "phone_1080p.h264")
        with open(path, "rb") as fh:
            self.assertIn(b"\x00\x00\x03", fh.read())  # real EPB
        info = parse(_first_sps(path))
        self.assertEqual(info["profile_idc"], 100)
        self.assertEqual(info["chroma_format_idc"], 1)
        self.assertEqual(info["bit_depth_luma"], 8)
        self.assertTrue(info["frame_mbs_only"])
        self.assertEqual(info["width"], 1920)
        self.assertEqual(info["fps"], "30")  # factor of two applied, not 60


class ErrorTests(unittest.TestCase):
    def test_non_sps_nal_rejected(self):
        with self.assertRaises(SpsError):
            parse(b"\x65" + b"\x00" * 4)

    def test_truncated_sps_rejected(self):
        with self.assertRaises(SpsError):
            parse(b"\x67\x42\x00")


if __name__ == "__main__":
    unittest.main()
