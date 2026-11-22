"""AV1 in IVF: IVF frames, OBUs (AV1 bitstream spec 5.3) and the sequence header (5.5)."""
import struct
from fractions import Fraction

OBU_NAMES = {1: "sequence_header", 2: "temporal_delimiter", 3: "frame_header", 4: "tile_group", 5: "metadata",
             6: "frame", 7: "redundant_frame_header", 8: "tile_list", 15: "padding"}


class Av1Error(ValueError):
    pass


def leb128(data, pos):
    """Read an unsigned LEB128 (spec 4.10.5); return (value, position after it)."""
    value = 0
    for shift in range(0, 64, 7):
        if pos >= len(data):
            raise Av1Error("truncated LEB128 value")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
    raise Av1Error("LEB128 value too long")


class Bits:
    def __init__(self, data):
        self.d, self.p = data, 0

    def f(self, n):
        if n == 0:
            return 0
        if (self.p + n) > len(self.d) << 3:
            raise Av1Error("truncated sequence header")
        v = 0
        for _ in range(n):
            byte = self.d[self.p >> 3]
            v = (v << 1) | ((byte >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def uvlc(self):
        # Unsigned variable-length code (spec 4.10.3). Leading 1 bit encodes 0;
        # otherwise n leading 0 bits, a 1, then n payload bits.
        leading_zeros = 0
        while self.f(1) == 0:
            leading_zeros += 1
            if leading_zeros >= 32:
                return (1 << 32) - 1
        payload = self.f(leading_zeros) if leading_zeros else 0
        return payload + (1 << leading_zeros) - 1


def _timing_info(b):
    num_units = b.f(32)
    time_scale = b.f(32)
    equal_picture_interval = bool(b.f(1))
    ticks_per_picture = b.uvlc() + 1 if equal_picture_interval else None
    return ({"num_units_in_display_tick": num_units, "time_scale": time_scale,
             "ticks_per_picture": ticks_per_picture}, equal_picture_interval)


def _decoder_model_info(b):
    buffer_delay = b.f(5) + 1
    b.f(32)                                    # num_units_in_decoding_tick
    b.f(5)                                     # buffer_removal_time_length_minus_1
    b.f(5)                                     # frame_presentation_time_length_minus_1
    return buffer_delay


def sequence_header(payload):
    """Parse sequence_header_obu payload per AV1 spec 5.5.1."""
    b = Bits(payload)
    out = {"profile": b.f(3), "still_picture": bool(b.f(1)),
           "reduced_still_picture_header": bool(b.f(1)),
           "timing": None, "operating_points": 1, "level": None, "tier": 0}

    if out["reduced_still_picture_header"]:
        # spec 6.4.1: a reduced header pins a number of syntax values.
        out["level"] = b.f(5)
        out["tier"] = 0
        decoder_model = False
        initial_delay = False
    else:
        timing_present = bool(b.f(1))
        decoder_model = False
        buffer_delay_len = 0
        if timing_present:
            out["timing"], _ = _timing_info(b)
            decoder_model = bool(b.f(1))                    # decoder_model_info_present_flag
            if decoder_model:
                buffer_delay_len = _decoder_model_info(b)
        # initial_display_delay_present_flag (spec 5.5.1): always signalled.
        initial_delay = bool(b.f(1))
        count = b.f(5) + 1                                  # operating_points_cnt_minus_1
        out["operating_points"] = count
        for i in range(count):
            b.f(12)                                         # operating_point_idc
            level = b.f(5)                                  # seq_level_idx[i]
            # seq_tier[i] is only signalled above level 7 (spec 5.5.1).
            tier = b.f(1) if level > 7 else 0
            if i == 0:
                out["level"], out["tier"] = level, tier
            if decoder_model and b.f(1):                    # decoder_model_present_for_this_op
                b.f(buffer_delay_len)                       # decoder_buffer_delay
                b.f(buffer_delay_len)                       # encoder_buffer_delay
                b.f(1)                                      # low_delay_mode_flag
            if initial_delay and b.f(1):                    # initial_display_delay_present_for_this_op
                b.f(4)                                      # initial_display_delay_minus_1

    # Frame dimensions. frame_id_numbers_present follows these in the bitstream.
    width_bits = b.f(4) + 1
    height_bits = b.f(4) + 1
    out["max_width"] = b.f(width_bits) + 1
    out["max_height"] = b.f(height_bits) + 1

    if not out["reduced_still_picture_header"]:
        if b.f(1):                                            # frame_id_numbers_present_flag
            b.f(4)                                          # delta_frame_id_length_minus_2
            b.f(3)                                          # additional_frame_id_length_minus_1

    out["superblock_128"] = bool(b.f(1))                    # use_128x128_superblock
    b.f(1)                                                  # enable_filter_intra
    b.f(1)                                                  # enable_intra_edge_filter

    if not out["reduced_still_picture_header"]:
        b.f(1)                                              # enable_interintra_compound
        b.f(1)                                              # enable_masked_compound
        b.f(1)                                              # enable_warped_motion
        b.f(1)                                              # enable_dual_filter
        enable_order_hint = bool(b.f(1))
        if enable_order_hint:
            b.f(1)                                          # enable_jnt_comp
            b.f(1)                                          # enable_ref_frame_mvs
        seq_choose_screen_tools = bool(b.f(1))
        if seq_choose_screen_tools:
            b.f(1)                                          # seq_choose_integer_mv
        else:
            force_screen_content_tools = b.f(1)             # seq_force_screen_content_tools
            if force_screen_content_tools:
                b.f(1)                                      # seq_choose_integer_mv
        if enable_order_hint:
            order_hint_bits = b.f(3) + 1                    # OrderHintBits
        else:
            order_hint_bits = None
    else:
        # Reduced header: enable_order_hint is inferred 1, OrderHintBits == 1.
        order_hint_bits = 1
    out["order_hint_bits"] = order_hint_bits

    # Feature flags between the tool block and color_config (spec 5.5.1).
    b.f(1)                                                  # enable_superres
    b.f(1)                                                  # enable_cdef
    b.f(1)                                                  # enable_restoration

    # color_config() (spec 5.5.2)
    high_bitdepth = bool(b.f(1))
    if out["profile"] == 2 and high_bitdepth:
        bit_depth = 12 if b.f(1) else 10                       # twelve_bit flag
    else:
        bit_depth = 10 if high_bitdepth else 8
    out["bit_depth"] = bit_depth

    if out["profile"] == 1:
        # Profile 1 (high): monochrome is not allowed and 4:4:4 is implied.
        mono_chrome = False
        color_description_present = bool(b.f(1))
        if color_description_present:
            color_primaries = b.f(8)
            transfer_characteristics = b.f(8)
            matrix_coefficients = b.f(8)
        else:
            color_primaries = transfer_characteristics = matrix_coefficients = 2
        color_range = bool(b.f(1))                          # color_full_range_flag
        subsampling_x = subsampling_y = 0
    else:
        mono_chrome = bool(b.f(1))                          # monochrome_flag
        color_description_present = bool(b.f(1))
        if color_description_present:
            color_primaries = b.f(8)
            transfer_characteristics = b.f(8)
            matrix_coefficients = b.f(8)
        else:
            color_primaries = transfer_characteristics = 2
            matrix_coefficients = 1 if mono_chrome else 2
        if mono_chrome:
            color_range = bool(b.f(1))                      # color_full_range_flag
            subsampling_x = subsampling_y = 1
        elif color_primaries == 1 and transfer_characteristics == 13 and matrix_coefficients == 0:
            color_range = True                              # SRGB implies full range
        else:
            color_range = bool(b.f(1))
            if out["profile"] == 0:
                subsampling_x = subsampling_y = 1
            elif bit_depth == 12:
                subsampling_x = bool(b.f(1))
                if subsampling_x:
                    subsampling_y = bool(b.f(1))
                else:
                    subsampling_y = 0
            else:
                subsampling_x, subsampling_y = 1, 0
            if subsampling_x and subsampling_y:
                b.f(2)                                      # chroma_sample_position
            b.f(1)                                          # separate_uv_delta_q
    out["mono_chrome"] = mono_chrome
    out["color_primaries"] = color_primaries
    out["transfer_characteristics"] = transfer_characteristics
    out["matrix_coefficients"] = matrix_coefficients
    out["color_range"] = color_range
    out["subsampling"] = ("mono" if mono_chrome else
                          {(0, 0): "4:4:4", (1, 0): "4:2:2", (1, 1): "4:2:0"}[(subsampling_x, subsampling_y)])

    out["film_grain"] = bool(b.f(1))                        # film_grain_params_present
    return out


def obus(data, start, end):
    pos, out = start, []
    while pos < end:
        h = data[pos]
        if h & 0x80:
            raise Av1Error("forbidden bit set in OBU header")
        otype, ext, has_size = (h >> 3) & 0xF, (h >> 2) & 1, (h >> 1) & 1
        p = pos + 1
        temporal = spatial = 0
        if ext:
            if p >= end:
                raise Av1Error("truncated OBU extension header")
            extension = data[p]
            # obu_temporal_id is the top 3 bits, obu_spatial_id the next 2.
            temporal, spatial = extension >> 5, (extension >> 3) & 3
            p += 1
        if has_size:
            size, p = leb128(data, p)
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
    if pts:
        step = pts[1] - pts[0] if len(pts) > 1 else 1
        # (last pts - first pts + first inter-frame step) converted via the
        # IVF time base, which is num/den seconds per tick.
        span = pts[-1] - pts[0] + step
        duration = str(Fraction(span * num, den))
    else:
        duration = "0"
    return {"fourcc": fourcc.decode("latin-1"), "width": width, "height": height,
            "timebase": "%d/%d" % (num, den), "ivf_frames": frames, "header_frame_count": nframes,
            "duration": duration,
            "obus": dict(sorted(counts.items())), "temporal_units": tus,
            "layers": len(layers), "sequence": seq}
