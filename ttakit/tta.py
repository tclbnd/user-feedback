"""True Audio (.tta) files: TTA1 header, seek table and frame layout."""
import struct
import zlib
from fractions import Fraction

FORMATS = {1: "pcm", 2: "pcm_encrypted", 3: "float"}


class TtaError(ValueError):
    pass


def id3v2_size(data):
    """Bytes taken by a leading ID3v2 tag (ID3v2.4), 0 when there is none."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    if any(byte & 0x80 for byte in data[6:10]):
        return 0
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    total = 10 + size
    if data[5] & 0x10:  # footer present
        total += 10
    return total


def parse(data):
    start = id3v2_size(data)
    if len(data) < start + 22 or data[start:start + 4] != b"TTA1":
        raise TtaError("not a TTA1 file")
    fmt, channels, bits, rate, samples, crc = struct.unpack_from("<HHHIII", data, start + 4)
    if fmt not in FORMATS:
        raise TtaError("unknown format %d" % fmt)
    if not channels or not rate:
        raise TtaError("bad header")
    frame_len = rate * 256 // 245
    frames = -(-samples // frame_len) if samples else 0
    last = samples - frame_len * (frames - 1) if frames else 0
    table_at = start + 22
    table_end = table_at + 4 * frames
    sizes = list(struct.unpack_from("<%dI" % frames, data, table_at)) if len(data) >= table_end + 4 else None
    table_ok = sizes is not None and zlib.crc32(data[table_at:table_end]) == struct.unpack_from("<I", data, table_end)[0]
    audio = sum(sizes) if sizes is not None else None
    return {"id3v2_bytes": start, "format": FORMATS[fmt], "channels": channels, "bits": bits, "sample_rate": rate,
            "samples": samples, "duration": str(Fraction(samples, rate)),
            "frame_samples": frame_len, "frames": frames, "last_frame_samples": last,
            "header_crc_ok": zlib.crc32(data[start:start + 18]) == crc,
            "seek_table_crc_ok": table_ok, "audio_bytes": audio,
            "complete": audio is not None and table_end + 4 + audio <= len(data)}
