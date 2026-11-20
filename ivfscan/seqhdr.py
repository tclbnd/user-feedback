"""Fields of an AV1 sequence header OBU (AV1 spec 5.5.1-5.5.4)."""


class Bits:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def f(self, n):
        if self.pos + n > len(self.data) * 8:
            raise IndexError("sequence header truncated")
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def uvlc(self):
        zeros = 0
        while self.f(1) == 0:
            zeros += 1
            if zeros >= 32:
                raise ValueError("invalid uvlc in sequence header")
        return (1 << zeros) - 1 + self.f(zeros)


def _timing_info(b):
    """timing_info() -- 5.5.2."""
    b.f(32)                      # num_units_in_display_tick
    b.f(32)                      # time_scale
    if b.f(1):                   # equal_picture_interval
        b.uvlc()                 # num_ticks_per_picture_minus_1


def _decoder_model_info(b):
    """decoder_model_info() -- 5.5.3. Returns buffer_delay_length_minus_1 + 1."""
    buffer_delay_length = b.f(5) + 1   # buffer_delay_length_minus_1
    b.f(32)                            # num_units_in_decoding_tick
    b.f(5)                             # buffer_removal_time_length_minus_1
    b.f(5)                             # frame_presentation_time_length_minus_1
    return buffer_delay_length


def _operating_parameters_info(b, buffer_delay_length):
    """operating_parameters_info( op ) -- 5.5.4."""
    b.f(buffer_delay_length)     # decoder_buffer_delay[ op ]
    b.f(buffer_delay_length)     # encoder_buffer_delay[ op ]
    b.f(1)                       # low_delay_mode_flag[ op ]


def parse(payload):
    b = Bits(payload)
    profile = b.f(3)
    still = b.f(1)
    reduced = b.f(1)
    decoder_model_present = 0
    initial_display_delay_present = 0
    if reduced:
        operating_points = 1
        level = b.f(5)           # seq_level_idx[ 0 ]
        tier = 0                 # seq_tier[ 0 ] is 0 for reduced headers
    else:
        timing_info_present = b.f(1)
        buffer_delay_length = 0
        if timing_info_present:
            _timing_info(b)
            decoder_model_present = b.f(1)
            if decoder_model_present:
                buffer_delay_length = _decoder_model_info(b)
        initial_display_delay_present = b.f(1)
        operating_points = b.f(5) + 1    # operating_points_cnt_minus_1
        level = tier = None
        for op in range(operating_points):
            b.f(12)                          # operating_point_idc[ op ]
            seq_level_idx = b.f(5)
            seq_tier = b.f(1) if seq_level_idx > 7 else 0
            if op == 0:
                level, tier = seq_level_idx, seq_tier
            if decoder_model_present:
                if b.f(1):                   # decoder_model_present_for_this_op
                    _operating_parameters_info(b, buffer_delay_length)
            if initial_display_delay_present:
                if b.f(1):                   # initial_display_delay_present_for_this_op
                    b.f(4)                   # initial_display_delay_minus_1
    width_bits = b.f(4) + 1              # frame_width_bits_minus_1
    height_bits = b.f(4) + 1             # frame_height_bits_minus_1
    return {"profile": profile, "still_picture": bool(still),
            "level": level, "tier": tier, "operating_points": operating_points,
            "max_width": b.f(width_bits) + 1, "max_height": b.f(height_bits) + 1}
