"""Video headers carried after start codes.

Parsing follows ISO/IEC 13818-2 (MPEG-2 video) and ISO/IEC 11172-2
(MPEG-1 video).  All rates and ratios are kept as exact values
(``fractions.Fraction``) so NTSC rates and non-integer sample aspect
ratios are never rounded.
"""
from fractions import Fraction

from .bits import BitReader

# Table 6-3 of 13818-2 / table 2-4 of 11172-2: frame_rate_code values.
FRAME_RATES = {
    1: Fraction(24, 1),
    2: Fraction(24000, 1001),
    3: Fraction(25, 1),
    4: Fraction(30000, 1001),
    5: Fraction(30, 1),
    6: Fraction(50, 1),
    7: Fraction(60000, 1001),
    8: Fraction(60, 1),
}

# 13818-2 table 6-3: aspect_ratio_information is a *display* aspect ratio.
DISPLAY_ASPECT_RATIOS = {
    1: Fraction(1, 1),
    2: Fraction(4, 3),
    3: Fraction(16, 9),
    4: Fraction(221, 100),
}

# 11172-2 table 2.4: aspect_ratio_information is a *sample* aspect ratio
# (pixel aspect ratio), given in the standard as decimal values.
SAMPLE_ASPECT_RATIOS_MPEG1 = {
    1: Fraction(1, 1),
    2: Fraction(1347, 2000),       # 0.6735
    3: Fraction(7031, 10000),      # 0.7031
    4: Fraction(1523, 2000),       # 0.7615
    5: Fraction(1611, 2000),       # 0.8055
    6: Fraction(8437, 10000),      # 0.8437
    7: Fraction(1787, 2000),       # 0.8935
    8: Fraction(9157, 10000),      # 0.9157
    9: Fraction(1963, 2000),       # 0.9815
    10: Fraction(2051, 2000),      # 1.0255
    11: Fraction(2139, 2000),      # 1.0695
    12: Fraction(219, 200),        # 1.0950
    13: Fraction(463, 400),        # 1.1575
    14: Fraction(2403, 2000),      # 1.2015
}

PICTURE_TYPES = {1: "I", 2: "P", 3: "B", 4: "D"}

# 13818-2 8.1.1 profile/level indication (escape value 0 is not a name).
PROFILES = {
    1: "High",
    2: "Spatial",
    3: "SNR",
    4: "Main",
    5: "Simple",
    10: "4:2:2",
}
LEVELS = {4: "High", 6: "High 1440", 8: "Main", 10: "Low"}

# 13818-2 6.3.10 chroma_format.
CHROMA_FORMATS = {1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}


def sequence_header(payload):
    """13818-2 6.2.2.1 / 11172-2 2.4.2.1 sequence_header()."""
    r = BitReader(payload)
    out = {
        "width": r.read(12),
        "height": r.read(12),
        "aspect_code": r.read(4),
        "frame_rate_code": r.read(4),
        "bit_rate_value": r.read(18),
        "marker_bit": r.read(1),
        "vbv_value": r.read(10),
        "constrained_parameters_flag": r.read(1),
    }
    # load_intra_quantiser_matrix_code / optional matrix bytes.
    if r.read(1):
        out["intra_quantiser_matrix"] = [r.read(8) for _ in range(64)]
    if r.read(1):
        out["non_intra_quantiser_matrix"] = [r.read(8) for _ in range(64)]
    return out


def sequence_extension(payload):
    """13818-2 6.2.2.3 sequence_extension()."""
    r = BitReader(payload)
    assert r.read(4) == 1  # extension_start_code_identifier
    return {
        "profile_and_level_indication": r.read(8),
        "progressive_sequence": r.read(1),
        "chroma_format": r.read(2),
        "horizontal_size_extension": r.read(2),
        "vertical_size_extension": r.read(2),
        "bit_rate_extension": r.read(12),
        "marker_bit": r.read(1),
        "vbv_buffer_size_extension": r.read(8),
        "low_delay": r.read(1),
        "frame_rate_extension_n": r.read(2),
        "frame_rate_extension_d": r.read(5),
    }


def sequence_display_extension(payload):
    """13818-2 6.2.2.2 sequence_display_extension()."""
    r = BitReader(payload)
    assert r.read(4) == 2
    out = {
        "video_format": r.read(3),
        "colour_description_flag": r.read(1),
    }
    if out["colour_description_flag"]:
        out["colour_primaries"] = r.read(8)
        out["transfer_characteristics"] = r.read(8)
        out["matrix_coefficients"] = r.read(8)
    out["display_horizontal_size"] = r.read(14)
    out["marker_bit"] = r.read(1)
    out["display_vertical_size"] = r.read(14)
    return out


def gop_header(payload):
    """13818-2 6.2.2.6 / 11172-2 2.4.2.4 group_of_pictures_header()."""
    r = BitReader(payload)
    time_code = {
        "drop_frame_flag": r.read(1),
        "time_code_hours": r.read(5),
        "time_code_minutes": r.read(6),
        "marker_bit": r.read(1),
        "time_code_seconds": r.read(6),
        "time_code_pictures": r.read(6),
    }
    return {
        "time_code": time_code,
        "closed_gop": r.read(1),
        "broken_link": r.read(1),
    }


def picture_header(payload):
    """13818-2 6.2.3 / 11172-2 2.4.3 picture_header() (shared layout)."""
    r = BitReader(payload)
    out = {
        "temporal_reference": r.read(10),
        "picture_coding_type": r.read(3),
        "vbv_delay": r.read(16),
    }
    out["type"] = PICTURE_TYPES.get(out["picture_coding_type"], "?")
    ptype = out["picture_coding_type"]
    if ptype in (2, 3):  # P or B picture
        out["full_pel_forward_vector"] = r.read(1)
        out["forward_f_code"] = r.read(3)
    if ptype == 3:       # B picture
        out["full_pel_backward_vector"] = r.read(1)
        out["backward_f_code"] = r.read(3)
    return out


def picture_coding_extension(payload):
    """13818-2 6.2.3.1 picture_coding_extension()."""
    r = BitReader(payload)
    assert r.read(4) == 8
    out = {"f_code": [[r.read(4), r.read(4)] for _ in range(2)]}
    out.update({
        "intra_dc_precision": r.read(2),
        "picture_structure": r.read(2),
        "top_field_first": r.read(1),
        "frame_pred_frame_dct": r.read(1),
        "concealment_motion_vectors": r.read(1),
        "q_scale_type": r.read(1),
        "intra_vlc_format": r.read(1),
        "alternate_scan": r.read(1),
        "repeat_first_field": r.read(1),
        "chroma_420_type": r.read(1),
        "progressive_frame": r.read(1),
        "composite_display_flag": r.read(1),
    })
    if out["composite_display_flag"]:
        out.update({
            "v_axis": r.read(1),
            "field_sequence": r.read(3),
            "sub_carrier": r.read(1),
            "burst_amplitude": r.read(7),
            "sub_carrier_phase": r.read(8),
        })
    return out
