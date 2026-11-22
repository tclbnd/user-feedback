"""AV1 in IVF: IVF frames, OBUs (AV1 bitstream spec 5.3) and the sequence header (5.5)."""
import struct
from fractions import Fraction

OBU_NAMES = {1: "sequence_header", 2: "temporal_delimiter", 3: "frame_header", 4: "tile_group", 5: "metadata",
             6: "frame", 7: "redundant_frame_header", 8: "tile_list", 15: "padding"}


class Av1Error(ValueError):
    pass


def leb128(data, pos):
    """Generic LEB128 unsigned integer (spec 4.10.5): up to 8 bytes, MSB continues."""
    value = 0
    for i in range(8):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value, pos
    raise Av1Error("truncated LEB128 integer")


class Bits:
    def __init__(self, data):
        self.d, self.p = data, 0

    def f(self, n):
        v = 0
        for _ in range(n):
            i = self.p >> 3
            if i >= len(self.d):
                raise Av1Error("truncated OBU payload")
            v = (v << 1) | ((self.d[i] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def uvlc(self):
        """Unsigned variable-length code (spec 4.10.3)."""
        zeros = 0
        while not self.f(1):
            zeros += 1
            if zeros > 32:
                raise Av1Error("uvlc escape too long")
        return self.f(zeros) + (1 << zeros) - 1


def sequence_header(payload):
    """Parse a sequence_header_obu payload (spec 5.5.1, timing 5.3.5, color_config 5.5.2)."""
    b = Bits(payload)
    out = {"profile": b.f(3), "still_picture": bool(b.f(1)),
           "reduced_still_picture_header": bool(b.f(1)),
           "timing": None, "operating_points": 1, "level": None, "tier": 0}
    reduced = out["reduced_still_picture_header"]
    decoder_model_present = False
    buffer_delay_len = 0
    initial_delay_present = False

    if reduced:
        out["level"] = b.f(5)                       # seq_level_idx[0]; seq_tier[0] = 0
    else:
        if b.f(1):                                  # timing_info_present_flag
            timing = {"num_units_in_display_tick": b.f(32), "time_scale": b.f(32),
                      "ticks_per_picture": None}
            if b.f(1):                              # equal_picture_interval_present_flag
                timing["ticks_per_picture"] = b.uvlc() + 1
            out["timing"] = timing
            decoder_model_present = bool(b.f(1))   # decoder_model_info_present_flag
            if decoder_model_present:               # decoder_model_info()
                buffer_delay_len = b.f(5) + 1
                b.f(32)                             # num_units_in_decoding_tick
                b.f(5)                              # buffer_removal_time_length_minus_1
                b.f(5)                              # frame_presentation_time_length_minus_1
        initial_delay_present = bool(b.f(1))        # initial_display_delay_present_flag
        count = b.f(5) + 1                          # operating_points_count_minus_1
        out["operating_points"] = count
        for i in range(count):
            idc = b.f(12)                           # operating_point_idc[i]
            level = b.f(5)                          # seq_level_idx[i]
            tier = b.f(1) if level > 7 else 0       # seq_tier[i] only above level 7
            if i == 0:
                out["level"], out["tier"] = level, tier
            if idc == 0:
                continue                            # operating_parameters_info() returns
            if decoder_model_present and b.f(1):    # decoder_model_present_for_this_op
                b.f(buffer_delay_len)               # encoder_buffer_delay
                b.f(buffer_delay_len)               # encoder_buffer_size
                if b.f(1):                          # initial_display_delay_present_for_this_op
                    b.f(4)                          # initial_display_delay_minus_1
            if initial_delay_present and b.f(1):
                b.f(4)                              # initial_display_delay_minus_1

    wbits, hbits = b.f(4) + 1, b.f(4) + 1
    out["max_width"] = b.f(wbits) + 1
    out["max_height"] = b.f(hbits) + 1

    if not reduced and b.f(1):                      # frame_id_numbers_present_flag
        b.f(4)                                      # delta_frame_id_length_minus_2
        b.f(3)                                      # additional_frame_id_length_minus_3

    out["superblock_128"] = bool(b.f(1))            # use_128x128_superblock
    b.f(1)                                          # enable_filter_intra
    b.f(1)                                          # enable_intra_edge_filter

    enable_order_hint = False
    if not reduced:
        b.f(1)                                      # enable_interintra_compound
        b.f(1)                                      # enable_masked_compound
        b.f(1)                                      # enable_warped_motion
        b.f(1)                                      # enable_dual_filter
        enable_order_hint = bool(b.f(1))
        if enable_order_hint:
            b.f(1)                                  # enable_jnt_comp
            b.f(1)                                  # enable_ref_frame_mvs
        choose_sct = b.f(1)                         # seq_choose_screen_content_tools
        force_sct = 1
        if not choose_sct:
            force_sct = b.f(1)                      # seq_force_screen_content_tools
        choose_integer_mv = 1
        if choose_sct or force_sct:
            choose_integer_mv = b.f(1)              # seq_choose_integer_mv
        if not choose_integer_mv:
            b.f(1)                                  # seq_force_integer_mv
        out["order_hint_bits"] = b.f(3) + 1 if enable_order_hint else None
    else:
        out["order_hint_bits"] = None

    b.f(1)                                          # enable_superres
    b.f(1)                                          # enable_cdef
    b.f(1)                                          # enable_restoration

    # color_config()
    high_bitdepth = bool(b.f(1))
    if out["profile"] == 2 and high_bitdepth:
        bit_depth = 12 if b.f(1) else 10            # twelve_bit
    else:
        bit_depth = 10 if high_bitdepth else 8
    out["bit_depth"] = bit_depth

    mono = False if out["profile"] == 1 else bool(b.f(1))
    out["mono_chrome"] = mono

    if b.f(1):                                      # color_description_present_flag
        cp, tc, mc = b.f(8), b.f(8), b.f(8)
    else:
        cp = tc = mc = 2                            # CP/TC/MC_UNSPECIFIED
    out["color_primaries"], out["transfer_characteristics"], out["matrix_coefficients"] = cp, tc, mc

    if mono:
        out["color_range"] = bool(b.f(1))           # full_range_flag
        sx = sy = 1
    elif cp == 1 and tc == 13 and mc == 0:          # BT.709 + sRGB + identity
        out["color_range"] = True
        if out["profile"] == 0:
            sx = sy = 1
        elif out["profile"] == 1:
            sx = sy = 0
        elif bit_depth == 12:
            sx = b.f(1)
            sy = b.f(1) if sx else 0
        else:
            sx, sy = 1, 0
    else:
        out["color_range"] = bool(b.f(1))
        if out["profile"] == 0:
            sx = sy = 1
        elif out["profile"] == 1:
            sx = sy = 0
        elif bit_depth == 12:
            sx = b.f(1)
            sy = b.f(1) if sx else 0
        else:
            sx, sy = 1, 0
        b.f(2)                                      # chroma_sample_position
    out["subsampling"] = ("mono" if mono else "4:2:0" if (sx, sy) == (1, 1)
                          else "4:2:2" if (sx, sy) == (1, 0) else "4:4:4")
    b.f(1)                                          # separate_uv_delta_q

    out["film_grain"] = bool(b.f(1))
    return out


def obus(data, start, end):
    pos, out = start, []
    while pos < end:
        h = data[pos]
        if h & 0x80:                                # obu_forbidden_bit
            raise Av1Error("forbidden bit set in OBU header")
        otype, ext, has_size = (h >> 3) & 0xF, (h >> 2) & 1, (h >> 1) & 1
        p = pos + 1
        temporal = spatial = 0
        if ext:                                     # obu_extension_header
            eh = data[p]
            temporal, spatial = eh >> 5, (eh >> 3) & 3
            p += 1
        if has_size:
            size, p = leb128(data, p)               # obu_size
        else:
            size = end - p
        if p + size > end:
            raise Av1Error("OBU runs past the end of its frame")
        out.append({"type": otype, "payload": data[p:p + size], "temporal": temporal, "spatial": spatial})
        pos = p + size
    return out


def scan(data):
    if data[:4] != b"DKIF" or len(data) < 32:
        raise Av1Error("not an IVF file")
    version, hlen, fourcc, width, height, den, num, nframes = struct.unpack_from("<HH4sHHIII", data, 4)
    if hlen < 32:
        raise Av1Error("IVF header too short")
    if num == 0 or den == 0:
        raise Av1Error("invalid IVF time base")
    pos, frames, counts, seq, tus, pts = hlen, 0, {}, None, 0, []
    layers = set()
    while pos + 12 <= len(data):
        size, ts = struct.unpack_from("<IQ", data, pos)
        body = pos + 12
        if body + size > len(data):
            break
        for o in obus(data, body, body + size):
            name = OBU_NAMES.get(o["type"], "reserved")
            counts[name] = counts.get(name, 0) + 1
            layers.add((o["temporal"], o["spatial"]))
            if o["type"] == 1 and seq is None:
                seq = sequence_header(o["payload"])
            if o["type"] == 2:
                tus += 1
        frames += 1
        pts.append(ts)
        pos = body + size
    # duration: (last pts - first pts + step of first two pts) ticks, times seconds/tick
    if pts:
        step = pts[1] - pts[0] if len(pts) > 1 else 1
        span = pts[-1] - pts[0] + step
        duration = str(Fraction(span * num, den))
    else:
        duration = "0"
    return {"fourcc": fourcc.decode("latin-1"), "width": width, "height": height,
            "timebase": "%d/%d" % (num, den), "ivf_frames": frames, "header_frame_count": nframes,
            "duration": duration, "obus": dict(sorted(counts.items())), "temporal_units": tus,
            "layers": len(layers), "sequence": seq}
