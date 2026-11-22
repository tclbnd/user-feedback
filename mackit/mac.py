"""Monkey's Audio (.ape) file headers.

Two header generations are supported:

* the APE descriptor layout written by encoders 3.98 and newer: an
  ``APE_DESCRIPTOR`` (id, version, sizes, 64-bit frame byte count, MD5) followed
  by a separate ``APE_HEADER`` that carries the audio format and frame layout;
* the single 36-byte header used by every older encoder (3.97 and earlier),
  whose 16-bit fields mix format and frame information and which may carry a
  peak level.

Files may additionally start with an ID3v2 tag sitting in front of ``MAC ``.
"""
import struct
from fractions import Fraction

LEVELS = {1000: "fast", 2000: "normal", 3000: "high", 4000: "extra high", 5000: "insane"}
FLAG_8BIT, FLAG_CRC, FLAG_PEAK, FLAG_24BIT, FLAG_SEEK, FLAG_NO_WAV = 1, 2, 4, 8, 16, 32

# Encoders before 3.98 did not store the blocks-per-frame value in the header;
# it was fixed by the encoder version (Monkey's Audio APEInfo::GetBPD).
OLD_BPF = ((3900, 73728 * 4), (3800, 73728), (0, 9216))

# Version from which the two-part APE descriptor / APE header is written.
DESCRIPTOR_VERSION = 3980


class MacError(ValueError):
    pass


def _id3v2_size(data):
    """Length of an ID3v2 tag prefix, including the 10-byte tag header."""
    if data[:3] != b"ID3" or len(data) < 10:
        return 0
    size_bytes = data[6:10]
    if any(byte & 0x80 for byte in size_bytes):
        raise MacError("invalid ID3v2 tag size")
    size = 0
    for byte in size_bytes:  # four syncsafe bytes
        size = (size << 7) | byte
    return 10 + size


def _old_blocks_per_frame(version):
    for threshold, blocks in OLD_BPF:
        if version >= threshold:
            return blocks
    return 9216


def _version_text(version):
    return "%d.%02d" % (version // 1000, version % 1000 // 10)


def parse(data):
    start = _id3v2_size(data)
    if data[start:start + 4] != b"MAC ":
        raise MacError("not a Monkey's Audio file")
    version, = struct.unpack_from("<H", data, start + 4)
    out = {"id3v2_bytes": start, "version": _version_text(version)}
    if version >= DESCRIPTOR_VERSION:
        return _parse_descriptor(data, start, version, out)
    return _parse_old(data, start, version, out)


def _seek_info(data, seek_at, count, file_size):
    entries = list(struct.unpack_from("<%dI" % count, data, seek_at)) if count else []
    inside = all(entry < file_size for entry in entries)
    increasing = all(a < b for a, b in zip(entries, entries[1:]))
    return entries, (inside and increasing)


def _parse_descriptor(data, start, version, out):
    # APE_DESCRIPTOR: cID(4) nVersion(2) nPadding(2) nDescriptorBytes ...
    # nHeaderBytes nSeekTableBytes nHeaderDataBytes nAPEFrameDataBytes(lo/hi)
    # nWAVTerminatingBytes, then the 16-byte MD5 at the descriptor tail.
    (_, _, desc_bytes, header_bytes, seek_bytes, wav_bytes,
     frame_lo, frame_hi, term_bytes) = struct.unpack_from("<HHIIIIIII", data, start + 4)
    # The MD5 lives at the fixed tail of the 36-byte descriptor core; any extra
    # descriptor padding declared by nDescriptorBytes follows it.
    md5 = data[start + 36:start + 52].hex()
    h = start + desc_bytes

    # APE_HEADER: nCompressionLevel(2) nFormatFlags(2) nBlocksPerFrame(4)
    # nFinalFrameBlocks(4) nTotalFrames(4) nBitsPerSample(2) nChannels(2)
    # nSampleRate(4)
    level, flags, bpf, final, frames, bits, ch, rate = \
        struct.unpack_from("<HHIIIHHI", data, h)
    seek_at = h + header_bytes
    entries, seek_ok = _seek_info(data, seek_at, seek_bytes // 4, len(data))

    total = (frames - 1) * bpf + final if frames else 0
    out.update(
        layout="descriptor",
        compression=LEVELS.get(level, str(level)),
        channels=ch,
        bits=bits,
        sample_rate=rate,
        blocks_per_frame=bpf,
        total_frames=frames,
        final_frame_blocks=final,
        total_blocks=total,
        duration=str(Fraction(total, rate)) if rate else None,
        seek_entries=len(entries),
        wav_header_bytes=wav_bytes,
        frame_data_bytes=frame_lo | (frame_hi << 32),
        md5=md5,
        peak_level=None,
        first_frame_offset=entries[0] if entries else None,
        seek_table_ok=seek_ok,
    )
    return out


def _parse_old(data, start, version, out):
    # Legacy single header (36 bytes): cID(4) nVersion(2) nCompressionLevel(2)
    # nFormatFlags(2) nChannels(2) nSampleRate(4) nHeaderBytes(4)
    # nTerminatingBytes(4) nTotalFrames(4) nFinalFrameBlocks(4) nPeakLevel(4).
    (magic, _, level, flags, ch, rate, wav_bytes, term_bytes,
     frames, final, peak) = struct.unpack_from("<4sHHHHIIIIII", data, start)
    bits = 8 if flags & FLAG_8BIT else 24 if flags & FLAG_24BIT else 16
    bpf = _old_blocks_per_frame(version)
    # The legacy seek table stores one offset per frame boundary, i.e. one
    # entry fewer than the total frame count.
    seek_entries = max(frames - 1, 0)
    seek_at = start + 36 + wav_bytes
    entries, seek_ok = _seek_info(data, seek_at, seek_entries, len(data))

    total = (frames - 1) * bpf + final if frames else 0
    out.update(
        layout="old",
        compression=LEVELS.get(level, str(level)),
        channels=ch,
        bits=bits,
        sample_rate=rate,
        blocks_per_frame=bpf,
        total_frames=frames,
        final_frame_blocks=final,
        total_blocks=total,
        duration=str(Fraction(total, rate)) if rate else None,
        seek_entries=len(entries),
        wav_header_bytes=wav_bytes,
        frame_data_bytes=None,
        md5=None,
        peak_level=peak if flags & FLAG_PEAK else None,
        first_frame_offset=entries[0] if entries else None,
        seek_table_ok=seek_ok,
    )
    return out
