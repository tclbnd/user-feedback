"""RAD Smacker video (.smk): header, frame tables, palettes, audio tracks."""
import struct
from fractions import Fraction


class SmkError(ValueError):
    pass


HEADER_SIZE = 104

# Bits 30..31 of an audio info word select the codec in SMK4 files.
_CODECS = ("pcm", "smacker", "bink-rdft", "bink-dct")
_FLAG_16BIT = 1 << 29
_FLAG_STEREO = 1 << 28
_CODEC_SHIFT = 30
_RATE_MASK = 0xFFFFFF


def _u32(data, pos, what):
    if pos + 4 > len(data):
        raise SmkError("%s runs past the end of the file" % what)
    return struct.unpack_from("<I", data, pos)[0]


def frame_rate(raw):
    """Frame rate from the signed header word.

    A positive value is the frame length in milliseconds, a negative value
    is the negative of the frame rate in frames per second.
    """
    if raw > 0:
        return Fraction(1000, raw)
    if raw < 0:
        return Fraction(-raw)
    raise SmkError("frame rate of zero is invalid")


def parse(data):
    if len(data) < HEADER_SIZE or data[:3] != b"SMK" or data[3:4] not in (b"2", b"4"):
        raise SmkError("not a Smacker file")
    width, height, frames, rate_raw, flags = struct.unpack_from("<IIIiI", data, 4)
    trees, = struct.unpack_from("<I", data, 52)
    audio_info = struct.unpack_from("<7I", data, 72)

    ring = bool(flags & 1)
    interlaced = bool(flags & 2)
    doubled = bool(flags & 4)
    display_height = height * 2 if (interlaced or doubled) else height

    n = frames + (1 if ring else 0)
    p = HEADER_SIZE
    if p + 5 * n > len(data):
        raise SmkError("frame tables run past the end of the file")
    frame_sizes = struct.unpack_from("<%dI" % n, data, p)
    frame_types = data[p + 4 * n:p + 5 * n]
    p += 5 * n + trees
    if p > len(data):
        raise SmkError("Huffman trees run past the end of the file")

    tracks = {}
    smk4 = data[3:4] == b"4"
    for t, info in enumerate(audio_info):
        if info:
            if smk4:
                codec = _CODECS[info >> _CODEC_SHIFT]
            else:
                codec = "smacker" if info & (1 << 31) else "pcm"
            tracks[t] = {"track": t,
                         "sample_rate": info & _RATE_MASK,
                         "bits": 16 if info & _FLAG_16BIT else 8,
                         "channels": 2 if info & _FLAG_STEREO else 1,
                         "codec": codec,
                         "bytes": 0}

    video_bytes = keyframes = palettes = 0
    for i in range(n):
        size = frame_sizes[i] & ~3
        start, end = p, p + size
        if end > len(data):
            raise SmkError("frame %d runs past the end of the file" % i)
        q = start
        typ = frame_types[i]
        if typ & 1:
            # Palette block: one count byte followed by count BGRA entries,
            # the whole block being 4 * count bytes.
            count = data[q]
            q += 4 * count
            if q > end:
                raise SmkError("frame %d palette runs past the frame" % i)
        for t in range(7):
            if typ & (2 << t):
                length = _u32(data, q, "frame %d audio header" % i)
                if length < 4 or q + length > end:
                    raise SmkError("frame %d audio chunk overruns the frame" % i)
                if i < frames and t in tracks:
                    tracks[t]["bytes"] += length - 4
                q += length
        if q > end:
            raise SmkError("frame %d chunks overrun the frame size" % i)
        if i < frames:
            video_bytes += end - q
            if frame_sizes[i] & 1:
                keyframes += 1
            if typ & 1:
                palettes += 1
        p = end

    fps = frame_rate(rate_raw)
    return {"version": data[:4].decode(),
            "width": width,
            "height": display_height,
            "frames": frames,
            "ring_frame": ring,
            "fps": str(fps),
            "duration": str(Fraction(frames, 1) / fps),
            "interlaced": interlaced,
            "keyframes": keyframes,
            "palette_frames": palettes,
            "video_bytes": video_bytes,
            "audio": [tracks[t] for t in sorted(tracks) if tracks[t]["bytes"]]}
