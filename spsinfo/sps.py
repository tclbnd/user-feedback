"""seq_parameter_set_rbsp() and the VUI fields the farm needs.

Implemented against ITU-T H.264 (2019 and later editions), section
7.3.2.1 (SPS), 7.3.2.1.1 (high-profile extension and scaling lists)
and Annex E (VUI parameters).
"""
from fractions import Fraction

from .bits import BitReader


class SpsError(ValueError):
    pass


# profile_idc values that carry the high-profile extension syntax of
# section 7.3.2.1.1 (chroma_format_idc, bit depths, scaling matrices).
HIGH_PROFILES = frozenset((100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135))

# Annex E Table E-1 (aspect_ratio_idc).  idc 255 carries an explicit SAR.
_SAR = {
    1: "1:1",
    2: "12:11",
    3: "10:11",
    4: "16:11",
    5: "40:33",
    6: "24:11",
    7: "20:11",
    8: "32:11",
    9: "80:33",
    10: "18:11",
    11: "15:11",
    12: "64:33",
    13: "160:99",
    14: "4:3",
    15: "3:2",
    16: "2:1",
}

# Table 6-1: chroma_format_idc -> (SubWidthC, SubHeightC).
_CHROMA_SUBSAMPLING = {0: (1, 1), 1: (2, 2), 2: (2, 1), 3: (1, 1)}


def _remove_emulation_prevention(ebsp):
    """Convert an EBSP (NAL payload) back into an RBSP (section 7.4.1)."""
    rbsp = bytearray()
    zeros = 0
    i = 0
    while i < len(ebsp):
        b = ebsp[i]
        if zeros >= 2 and b == 0x03 and i + 1 < len(ebsp):
            # emulation_prevention_three_byte: drop the 03, keep next byte
            i += 1
            zeros = 0
            continue
        rbsp.append(b)
        zeros = zeros + 1 if b == 0 else 0
        i += 1
    return bytes(rbsp)


def _scaling_list(r, size, dc):
    """scaling_list() of section 7.3.2.1.1.1; returns (last_scale, next_dc)."""
    last_scale = 8
    next_scale = 8
    j = 0
    while j < size and next_scale != 0:
        delta_scale = r.se()
        next_scale = (last_scale + delta_scale + 256) % 256
        if next_scale != 0:
            last_scale = next_scale
        if size == 16 and j == 0:
            dc = next_scale
        j += 1
    # next_scale == 0 terminates the list: remaining entries are inferred
    # and are not present in the bitstream.
    return last_scale, dc


def _parse_scaling_matrices(r, chroma_format_idc, separate_colour_plane):
    """seq_scaling_matrix_present_flag parsing (section 7.3.2.1.1)."""
    if not r.flag():
        return
    # 4:4:4 with separate colour planes carries three independent plane sets.
    n_lists = 12 if (separate_colour_plane and chroma_format_idc == 3) else 8
    dc = 16
    for i in range(n_lists):
        if r.flag():  # seq_scaling_list_present_flag[i]
            size = 16 if i < 6 else 64
            _, dc = _scaling_list(r, size, dc)


def _parse_hrd(r):
    """hrd_parameters() of section E.1.2."""
    cp_cnt = r.ue() + 1
    r.u(4)   # bit_rate_scale
    r.u(4)   # cpb_size_scale
    for _ in range(cp_cnt):
        r.ue()  # bit_rate_value_minus1
        r.ue()  # cpb_size_value_minus1
        r.flag()  # cbr_flag
    r.u(5)   # initial_cpb_removal_delay_length_minus1
    r.u(5)   # cpb_removal_delay_length_minus1
    r.u(5)   # dpb_output_delay_length_minus1
    r.u(5)   # time_offset_length


def _parse_vui(r):
    """vui_parameters() of Annex E; returns the fields the farm uses."""
    out = {"sar": "1:1", "fps": None, "full_range": False}

    def present():
        # A present flag is never read once the RBSP trailing bits are reached.
        if r.at_rbsp_trailing():
            return False
        return r.flag()

    if present():  # aspect_ratio_info_present_flag
        idc = r.u(8)
        if idc == 255:  # Extended_SAR
            sar_w = r.u(16)
            sar_h = r.u(16)
            out["sar"] = "%d:%d" % (sar_w, sar_h)
        elif idc in _SAR:
            out["sar"] = _SAR[idc]
        else:  # 0 (Unspecified) or a reserved idc
            out["sar"] = "unknown"

    if present():  # overscan_info_present_flag
        r.flag()  # overscan_appropriate_flag

    if present():  # video_signal_type_present_flag
        r.u(3)  # video_format
        out["full_range"] = r.flag()  # video_full_range_flag
        if r.flag():  # colour_description_present_flag
            r.u(8)  # colour_primaries
            r.u(8)  # transfer_characteristics
            r.u(8)  # matrix_coefficients

    if present():  # chroma_loc_info_present_flag
        r.ue()  # chroma_sample_loc_type_top_field
        r.ue()  # chroma_sample_loc_type_bottom_field

    if present():  # timing_info_present_flag
        units = r.u(32)  # num_units_in_tick
        scale = r.u(32)  # time_scale
        fixed = r.flag()  # fixed_frame_rate_flag
        if units:
            # E.2.1: delta_tfi_divisor is 1 (fixed frame rate) or 2, so the
            # frame rate is time_scale / (2 * num_units_in_tick).
            out["fps"] = str(Fraction(scale, 2 * units))
        out["fixed_frame_rate"] = fixed

    if present():  # nal_hrd_parameters_present_flag
        _parse_hrd(r)
    if present():  # vcl_hrd_parameters_present_flag
        _parse_hrd(r)
    if present():  # low_delay_hrd_flag
        pass
    if present():  # pic_struct_present_flag
        pass

    if present():  # bitstream_restriction_flag
        r.flag()  # motion_vectors_over_pic_boundaries_flag
        r.ue()  # max_bytes_per_pic_denom
        r.ue()  # max_bits_per_mb_denom
        r.ue()  # log2_max_mv_length_horizontal
        r.ue()  # log2_max_mv_length_vertical

    return out


def _level_name(level_idc, constraint_flags):
    """Map level_idc to the Annex A level name, including level 1b."""
    if level_idc == 11:
        return "1b"
    if level_idc == 9 and (constraint_flags & 0x10):
        # level_idc 9 with constraint_set3_flag set also signals level 1b.
        return "1b"
    return str(level_idc / 10).rstrip("0").rstrip(".")


def parse(nal):
    if not nal or nal[0] & 0x1F != 7:
        raise SpsError("not an SPS NAL unit")
    try:
        rbsp = _remove_emulation_prevention(nal[1:])
        r = BitReader(rbsp)

        profile = r.u(8)
        constraints = r.u(8)
        level_idc = r.u(8)
        r.ue()  # sps_id

        chroma = 1
        depth_l = depth_c = 8
        separate_colour_plane = False
        if profile in HIGH_PROFILES:
            chroma = r.ue()  # chroma_format_idc
            if chroma not in _CHROMA_SUBSAMPLING:
                raise SpsError("bad chroma_format_idc %d" % chroma)
            # separate_colour_plane_flag is only present for 4:4:4 (7.3.2.1.1).
            if chroma == 3:
                separate_colour_plane = r.flag()
            depth_l = r.ue() + 8  # bit_depth_luma_minus8
            depth_c = r.ue() + 8  # bit_depth_chroma_minus8
            r.flag()  # qpprime_y_zero_transform_bypass_flag
            _parse_scaling_matrices(r, chroma, separate_colour_plane)

        r.ue()  # log2_max_frame_num_minus4
        poc_type = r.ue()  # pic_order_cnt_type
        if poc_type == 0:
            r.ue()  # log2_max_pic_order_cnt_lsb_minus4
        elif poc_type == 1:
            r.flag()  # delta_pic_order_always_zero_flag
            r.se()  # offset_for_non_ref_pic
            r.se()  # offset_for_top_to_bottom_field
            n_ref = r.ue()  # num_ref_frames_in_pic_order_cnt_cycle
            for _ in range(n_ref):
                r.se()  # offset_for_ref_frame
        elif poc_type != 2:
            raise SpsError("bad pic_order_cnt_type %d" % poc_type)

        r.ue()  # max_num_ref_frames
        r.flag()  # gaps_in_frame_num_value_allowed_flag
        w_mbs = r.ue() + 1  # pic_width_in_mbs_minus1
        h_units = r.ue() + 1  # pic_height_in_map_units_minus1
        frame_mbs_only = r.flag()
        if not frame_mbs_only:
            r.flag()  # mb_adaptive_frame_field_flag
        r.flag()  # direct_8x8_inference_flag

        left = right = top = bottom = 0
        if r.flag():  # frame_cropping_flag
            left = r.ue()  # frame_crop_left_offset
            right = r.ue()  # frame_crop_right_offset
            top = r.ue()  # frame_crop_top_offset
            bottom = r.ue()  # frame_crop_bottom_offset

        # Frame height in macroblocks is twice the map-unit count when fields
        # are allowed (7.4.2.1.1 frame dimension / frame_crop equations).
        frame_height_mbs = h_units * (2 - int(frame_mbs_only))
        crop_w, crop_h = _CHROMA_SUBSAMPLING[chroma]
        if separate_colour_plane and chroma == 3:
            crop_w = crop_h = 1
        width = w_mbs * 16 - (left + right) * 2 * crop_w
        height = frame_height_mbs * 16 - (top + bottom) * 2 * crop_h

        out = {
            "profile_idc": profile,
            "constraint_flags": constraints,
            "level": _level_name(level_idc, constraints),
            "chroma_format_idc": chroma,
            "bit_depth_luma": depth_l,
            "bit_depth_chroma": depth_c,
            "frame_mbs_only": frame_mbs_only,
            "width": width,
            "height": height,
            "sar": "1:1",
            "fps": None,
            "full_range": False,
        }

        if r.flag():  # vui_parameters_present_flag
            out.update(_parse_vui(r))
        return out
    except IndexError as exc:
        raise SpsError("truncated SPS: %s" % exc) from exc
