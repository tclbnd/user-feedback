"""CRI ADX audio: header, versions 3 / 4 / 5 loop information, encryption, frames."""
import struct
from fractions import Fraction

ENCODINGS = {2: "adx_preset", 3: "adx_standard", 4: "adx_exponential", 0x10: "ahx", 0x11: "ahx"}


class AdxError(ValueError):
    pass


# Loop information sits at a fixed offset that depends on the header version
# (newer CRI tools write a larger fixed header before it).
LOOP_INFO_OFFSET = {3: 0x18, 4: 0x24, 5: 0x30}

ENCRYPTED_FLAG = 0x08


def parse(data):
    if len(data) < 0x14 or data[:2] != b"\x80\x00":
        raise AdxError("not an ADX file")
    copy_off, enc, block, bits, channels, rate, total, highpass, version, flags = struct.unpack_from(">HBBBBIIHBB", data, 2)
    # The copyright offset points two bytes past the "(c)CRI" signature;
    # the audio frames start right after it.
    start = copy_off + 4
    out = {"encoding": ENCODINGS.get(enc, "type_%d" % enc), "block_size": block, "bit_depth": bits, "channels": channels,
           "sample_rate": rate, "total_samples": total, "duration": str(Fraction(total, rate)) if rate else None,
           "highpass": highpass, "version": version, "encrypted": bool(flags & ENCRYPTED_FLAG),
           "copyright_ok": data[copy_off - 2:copy_off + 4] == b"(c)CRI", "data_offset": start,
           "loop": False, "loop_start": None, "loop_end": None, "loop_start_byte": None, "loop_end_byte": None,
           "samples_per_block": None, "data_frames": 0, "data_samples": 0}
    base = LOOP_INFO_OFFSET.get(version)
    if base is not None and base + 20 <= start:
        enabled, ls, lsb, le, leb = struct.unpack_from(">IIIII", data, base)
        if enabled:
            out.update(loop=True, loop_start=ls, loop_end=le, loop_start_byte=lsb, loop_end_byte=leb)
    if block and bits and channels:
        # Each per-channel block is a 2-byte header plus packed samples.
        per = (block - 2) * 8 // bits
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
