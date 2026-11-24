"""CRI ADX audio: header, versions 3 / 4 / 5 loop information, encryption, frames."""
import struct
from fractions import Fraction

ENCODINGS = {2: "adx_preset", 3: "adx_standard", 4: "adx_exponential", 0x10: "ahx", 0x11: "ahx"}


class AdxError(ValueError):
    pass


def parse(data):
    if len(data) < 0x14 or data[:2] != b"\x80\x00":
        raise AdxError("not an ADX file")
    copy_off, enc, block, bits, channels, rate, total, highpass, version, flags = struct.unpack_from(">HBBBBHxxIHBB", data, 2)
    start = copy_off
    out = {"encoding": ENCODINGS.get(enc, "type_%d" % enc), "block_size": block, "bit_depth": bits, "channels": channels,
           "sample_rate": rate, "total_samples": total, "duration": str(Fraction(total, rate)) if rate else None,
           "highpass": highpass, "version": version, "encrypted": False,
           "copyright_ok": data[start - 6:start] == b"(c)CRI", "data_offset": start,
           "loop": False, "loop_start": None, "loop_end": None, "loop_start_byte": None, "loop_end_byte": None,
           "samples_per_block": None, "data_frames": 0, "data_samples": 0}
    base = 0x18
    if base + 20 <= start:
        enabled, ls, lsb, le, leb = struct.unpack_from(">IIIII", data, base)
        if enabled:
            out.update(loop=True, loop_start=ls, loop_end=le, loop_start_byte=lsb, loop_end_byte=leb)
    if block and bits and channels:
        per = 32
        out["samples_per_block"] = per
        frames = 0
        p = start
        while p + block * channels <= len(data):
            if struct.unpack_from(">H", data, p)[0] == 0x8001:   # end-of-stream frame
                break
            frames += 1
            p += block * channels
        out["data_frames"] = frames
        out["data_samples"] = frames * per
    return out
