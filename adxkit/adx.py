"""CRI ADX audio: header, versions 3 / 4 / 5 loop information, encryption, frames."""
import math
import struct
from fractions import Fraction

ENCODINGS = {2: "adx_preset", 3: "adx_standard", 4: "adx_exponential", 0x10: "ahx", 0x11: "ahx"}
COEFF_FRAC = 1 << 12                      # coefficients are 1.12 fixed point
END_OF_STREAM = 0x8001                    # first scale word of the terminator frame
SIGNATURE = b"(c)CRI"


class AdxError(ValueError):
    pass


def _round_half_away(value):
    """Round to the nearest integer, ties going away from zero."""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def prediction_coefficients(sample_rate, highpass):
    """Two ADX prediction coefficients from the high-pass cutoff.

    CRI's filter derivation: a = sqrt(2) - cos(w),
    c = (a - sqrt(sqrt(2)*a - 1))**2 - 1, with w = 2*pi*highpass/sample_rate.
    """
    if not sample_rate or not highpass:
        return None
    w = 2.0 * math.pi * highpass / sample_rate
    a = math.sqrt(2.0) - math.cos(w)
    radicand = math.sqrt(2.0) * a - 1.0
    if radicand < 0:
        radicand = 0.0
    c = (a - math.sqrt(radicand)) ** 2 - 1.0
    c1 = _round_half_away(c * COEFF_FRAC)
    c2 = _round_half_away(-(c * c) * COEFF_FRAC)
    if c1 == 0 or c2 == 0:
        return None
    return c1, c2


def parse(data):
    if len(data) < 0x14 or data[:2] != b"\x80\x00":
        raise AdxError("not an ADX file")
    # 0x02 copyright offset (u16); encoding/block/bits/channels as bytes;
    # sample rate (u32), total samples (u32), highpass (u16), version, flags.
    copy_off, enc, block, bits, channels = struct.unpack_from(">HBBBB", data, 2)
    sample_rate, total, highpass, version, flags = struct.unpack_from(">IIHBB", data, 8)
    # The copyright offset lands two bytes into the 6-byte "(c)CRI" signature;
    # the signature therefore starts at copy_off - 2 and the first audio frame
    # begins in the two bytes following it, at copy_off + 4.
    sig_start = copy_off - 2
    start = sig_start + len(SIGNATURE)
    signature_ok = sig_start >= 0 and data[sig_start:sig_start + len(SIGNATURE)] == SIGNATURE

    out = {"encoding": ENCODINGS.get(enc, "type_%d" % enc), "block_size": block,
           "bit_depth": bits, "channels": channels, "sample_rate": sample_rate,
           "total_samples": total,
           "duration": str(Fraction(total, sample_rate)) if sample_rate else None,
           "highpass": highpass, "version": version, "encrypted": False,
           "copyright_ok": signature_ok, "data_offset": start,
           "loop": False, "loop_start": None, "loop_end": None,
           "loop_start_byte": None, "loop_end_byte": None,
           "samples_per_block": None, "data_frames": 0, "data_samples": 0,
           "key_type": None, "coefficients": None, "max_scale": None,
           "silent_blocks": 0}

    if version == 5:
        # Version 5 holds an encryption record before the loop information.
        # Newer v5 files keep 16 zero bytes at 0x14 and move the record to 0x24.
        enc_off = 0x14
        if len(data) >= 0x24 + 8 and struct.unpack_from(">4I", data, 0x14) == (0, 0, 0, 0):
            enc_off = 0x24
        if len(data) >= enc_off + 8:
            key_type, _seed, _mult, _add = struct.unpack_from(">IIII", data, enc_off)
            if key_type in (8, 9):
                out["encrypted"] = True
                out["key_type"] = key_type
        loop_base = enc_off + 0x10
    elif version == 3:
        loop_base = 0x14
    else:
        loop_base = 0x24             # version 4 and other v4-style headers

    # Loop record: enabled, start sample, start byte, end sample, end byte.
    # It counts only when the header has room for it and enabled is actually set.
    if loop_base + 20 <= start and len(data) >= loop_base + 20:
        enabled, ls, lsb, le, leb = struct.unpack_from(">IIIII", data, loop_base)
        if enabled:
            out.update(loop=True, loop_start=ls, loop_end=le,
                       loop_start_byte=lsb, loop_end_byte=leb)

    if enc in (3, 4):
        coeffs = prediction_coefficients(sample_rate, highpass)
        if coeffs is not None:
            out["coefficients"] = list(coeffs)

    # A block is 2 scale bytes plus bit_depth bits per sample, so one channel
    # block of block_size bytes holds (block_size - 2) * 8 / bit_depth samples.
    if block and bits and channels:
        per = (block - 2) * 8 // bits
        out["samples_per_block"] = per
        frame_size = block * channels
        frames = 0
        max_scale = None
        silent = 0
        p = start
        while p + frame_size <= len(data):
            if struct.unpack_from(">H", data, p)[0] == END_OF_STREAM:
                break                            # terminator frame, no audio data
            for ch in range(channels):
                scale = struct.unpack_from(">H", data, p + ch * block)[0]
                if max_scale is None or scale > max_scale:
                    max_scale = scale
                if scale == 0:
                    silent += 1
            frames += 1
            p += frame_size
        out["data_frames"] = frames
        out["data_samples"] = frames * per
        out["max_scale"] = max_scale
        out["silent_blocks"] = silent
    return out
