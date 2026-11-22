"""Monkey's Audio (.ape) file headers: the descriptor layout (3.98+) and the older single header."""
import struct
from fractions import Fraction

LEVELS = {1000: "fast", 2000: "normal", 3000: "high", 4000: "extra high", 5000: "insane"}
FLAG_8BIT, FLAG_CRC, FLAG_PEAK, FLAG_24BIT, FLAG_SEEK, FLAG_NO_WAV = 1, 2, 4, 8, 16, 32


class MacError(ValueError):
    pass


def parse(data):
    start = 0
    if data[start:start + 4] != b"MAC ":
        raise MacError("not a Monkey's Audio file")
    version, = struct.unpack_from("<H", data, start + 4)
    out = {"id3v2_bytes": start, "version": "%d.%02d" % (version // 1000, version % 1000 // 10)}
    (_, _, desc_bytes, header_bytes, seek_bytes, wav_bytes, frame_lo, frame_hi, term_bytes) = \
        struct.unpack_from("<HHIIIIIII", data, start + 4)
    md5 = data[start + 36:start + 52].hex()
    h = start + desc_bytes
    level, flags, bpf, final, frames, _, ch, rate = struct.unpack_from("<HHIIIHHI", data, h)
    bits = 8 if flags & FLAG_8BIT else 24 if flags & FLAG_24BIT else 16
    out.update(layout="descriptor", seek_entries=seek_bytes // 4, wav_header_bytes=wav_bytes,
               frame_data_bytes=frame_lo, md5=md5, peak_level=None)
    seek_at = start + desc_bytes + header_bytes
    n = out["seek_entries"]
    entries = struct.unpack_from("<%dI" % n, data, seek_at)
    out["first_frame_offset"] = entries[0] if n else None
    out["seek_table_ok"] = all(a < b for a, b in zip(entries, entries[1:]))
    blocks = frames * bpf
    out.update(compression=LEVELS.get(level, str(level)), channels=ch, bits=bits, sample_rate=rate,
               blocks_per_frame=bpf, total_frames=frames, final_frame_blocks=final, total_blocks=blocks,
               duration=str(Fraction(blocks, rate)) if rate else None)
    return out
