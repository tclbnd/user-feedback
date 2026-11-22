"""Apple Core Audio Format (.caf): chunks, audio description, packet table, info strings."""
import struct
from fractions import Fraction


class CafError(ValueError):
    pass


def chunks(data):
    if data[:4] != b"caff" or len(data) < 8:
        raise CafError("not a CAF file")
    pos, out = 8, []
    while pos + 12 <= len(data):
        ctype = data[pos:pos + 4].decode("latin-1")
        size, = struct.unpack_from(">q", data, pos + 4)
        body = pos + 12
        if size < 0:
            # A size of -1 means the chunk (normally 'data') runs to the end
            # of the file, as written by streaming/recording tools.
            size = len(data) - body
        elif body + size > len(data):
            # A recorder that lost power leaves the final chunk truncated; its
            # declared size may run past the end of the file.  Clamp it to the
            # bytes that are actually present instead of rejecting the file.
            size = len(data) - body
        out.append((ctype, body, size))
        pos = body + size
    return out


def varint(data, pos, end=None):
    """CAF variable-length integer: big-endian, 7 payload bits per byte,
    continuation bit 0x80 set on every byte except the last one."""
    value = 0
    while True:
        if pos >= len(data) or (end is not None and pos >= end):
            raise CafError("truncated variable-length integer")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


# ISO/IEC 14496-3 samplingFrequencyIndex table (index 4 is 44100, not 48000).
RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
         16000, 12000, 11025, 8000, 7350)


class _BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def bits(self, n):
        value = 0
        for _ in range(n):
            if self.pos >= len(self.data) * 8:
                raise CafError("truncated audio specific config")
            byte = self.data[self.pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def audio_object_type(self):
        aot = self.bits(5)
        if aot == 31:  # escape value: real type follows in 6 bits
            aot = 32 + self.bits(6)
        return aot

    def sample_rate(self):
        index = self.bits(4)
        if index == 0x0F:
            return self.bits(24)
        return RATES[index] if index < len(RATES) else None


def _audio_specific_config(buf):
    """Parse an ISO/IEC 14496-3 AudioSpecificConfig.

    Returns (core object type, sbr, ps, core sample rate,
    output sample rate, channel config)."""
    br = _BitReader(buf)
    obj = br.audio_object_type()
    if obj in (5, 29):  # explicit HE-AAC v1 (SBR) / HE-AAC v2 (SBR+PS)
        core_obj = br.audio_object_type()
        sbr, ps = True, obj == 29
        # extensionSamplingFrequency (the SBR output), then the core config
        output_rate = br.sample_rate()
        core_rate = br.sample_rate()
        channel_config = br.bits(4)
        return core_obj, sbr, ps, core_rate, output_rate, channel_config
    rate = br.sample_rate()
    channel_config = br.bits(4)
    return obj, False, False, rate, rate, channel_config


def _cookie(data, b, e):
    """AAC magic cookie: ES_Descriptor (0x03) > DecoderConfigDescriptor (0x04)
    > DecoderSpecificInfo (0x05) holding the AudioSpecificConfig."""
    p = b
    while p + 2 <= e:
        tag, n = data[p], data[p + 1]
        if tag == 0x03:
            p += 2 + 3
        elif tag == 0x04:
            p += 2 + 13
        elif tag == 0x05:
            obj, sbr, ps, rate, output_rate, cfg = _audio_specific_config(data[p + 2:p + 2 + n])
            return {"object_type": obj, "sbr": sbr, "ps": ps, "sample_rate": rate,
                    "output_sample_rate": output_rate, "channel_config": cfg}
        else:
            p += 2 + n
    return None


def parse(data):
    found = chunks(data)
    if not found or found[0][0] != "desc":
        raise CafError("the first chunk must be desc")
    _, b, _ = found[0]
    rate, fmt, flags, bpp, fpp, ch, bits = struct.unpack_from(">d4sIIIII", data, b)
    out = {"sample_rate": rate, "format": fmt.decode("latin-1").strip(), "channels": ch, "bits": bits,
           "bytes_per_packet": bpp, "frames_per_packet": fpp, "float": bool(flags & 1) if fmt == b"lpcm" else None,
           "little_endian": bool(flags & 2) if fmt == b"lpcm" else None,
           "chunks": [c for c, _, _ in found], "edit_count": None, "data_bytes": None,
           "packets": None, "frames": None, "priming": 0, "remainder": 0, "duration": None,
           "max_packet_bytes": bpp or None, "max_packet_frames": fpp or None,
           "info": {}, "channel_layout": None, "aac": None}
    pakt = None
    for ctype, body, size in found:
        end = body + size
        if ctype == "data":
            # The data chunk starts with a 4-byte edit count; only the bytes
            # after it are audio data.  A chunk declared with size -1 or
            # truncated by a power loss is already clamped by chunks().
            if size < 4:
                raise CafError("data chunk is too short for the edit count")
            out["edit_count"], = struct.unpack_from(">I", data, body)
            out["data_bytes"] = size - 4
        elif ctype == "pakt":
            npk, valid, prime, rem = struct.unpack_from(">qqii", data, body)
            p = body + 24
            sizes, frames = [], []
            for _ in range(npk):
                # One pair of variable-length values per packet: size is always
                # present, frames are present only when they vary per packet
                # (frames_per_packet == 0 in the description).
                s, p = varint(data, p, end)
                sizes.append(s)
                if fpp == 0:
                    fr, p = varint(data, p, end)
                    frames.append(fr)
            pakt = {"packets": npk, "valid": valid, "priming": prime,
                    "remainder": rem, "sizes": sizes, "frames": frames}
        elif ctype == "info":
            # All CAF integers are big-endian, including the info entry count.
            n, = struct.unpack_from(">I", data, body)
            parts = data[body + 4:end].split(b"\0")
            for k in range(n):
                out["info"][parts[2 * k].decode("utf-8")] = parts[2 * k + 1].decode("utf-8")
        elif ctype == "kuki" and fmt.strip() == b"aac":
            out["aac"] = _cookie(data, body, end)
        elif ctype == "chan":
            tag, bitmap = struct.unpack_from(">II", data, body)
            out["channel_layout"] = {"tag": tag, "bitmap": bitmap}
    if pakt is not None:
        out["packets"] = pakt["packets"]
        out["priming"] = pakt["priming"]
        out["remainder"] = pakt["remainder"]
        if pakt["sizes"]:
            out["max_packet_bytes"] = max(pakt["sizes"])
        if pakt["frames"]:
            out["max_packet_frames"] = max(pakt["frames"])
        # mNumberValidFrames already excludes the encoder priming and the
        # trailing remainder; fall back to the declared packet geometry when a
        # writer leaves the field at zero.
        if pakt["valid"]:
            out["frames"] = pakt["valid"]
        else:
            out["frames"] = max(0, pakt["packets"] * fpp - pakt["priming"] - pakt["remainder"])
    elif bpp and out["data_bytes"] is not None:
        # Constant-size packets: data_bytes excludes the 4-byte edit count.
        out["packets"] = out["data_bytes"] // bpp
        out["frames"] = out["packets"] * fpp
    if out["frames"] is not None:
        out["duration"] = str(Fraction(out["frames"]) / Fraction(rate))
    return out
