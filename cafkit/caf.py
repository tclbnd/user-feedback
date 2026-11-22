"""Apple Core Audio Format (.caf): chunks, audio description, packet table, info strings.

Layout and semantics follow Apple's CAFFile.h and the Core Audio Format spec:

* every field is big endian;
* a negative chunk size means the chunk runs to the end of the file, which is how
  a recorder keeps a file well defined when it is interrupted;
* the ``data`` chunk starts with a 4-byte edit count that is not audio;
* ``pakt`` entries are big-endian base-128 variable length integers whose content
  depends on which of bytes/frames per packet are zero in ``desc``;
* ``info`` starts with a big-endian entry count followed by NUL terminated strings.
"""
import math
import struct
from fractions import Fraction


class CafError(ValueError):
    pass


FILE_HEADER_SIZE = 8
CHUNK_HEADER_SIZE = 12
DESC_SIZE = 32
PAKT_HEADER_SIZE = 24


def chunks(data):
    """Return ``(type, body offset, body length)`` for each chunk in the file.

    A negative size means "to the end of the file".  A chunk whose declared size
    is larger than the bytes on disk is a write that was cut short, so it is
    clipped to what is actually there.
    """
    if len(data) < FILE_HEADER_SIZE or data[:4] != b"caff":
        raise CafError("not a CAF file")
    pos, out = FILE_HEADER_SIZE, []
    while pos + CHUNK_HEADER_SIZE <= len(data):
        ctype = data[pos:pos + 4].decode("latin-1")
        size, = struct.unpack_from(">q", data, pos + 4)
        body = pos + CHUNK_HEADER_SIZE
        if size < 0 or body + size > len(data):
            size = len(data) - body
        out.append((ctype, body, size))
        pos = body + size
    return out


def varint(data, pos, end=None):
    """Decode a base-128 variable length integer, most significant group first."""
    if end is None:
        end = len(data)
    value = 0
    for _ in range(10):
        if pos >= end:
            return None, pos
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise CafError("variable length integer is too long")


RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


class _Bits:
    """MSB-first bit reader for an MPEG-4 AudioSpecificConfig."""

    __slots__ = ("data", "pos", "end")

    def __init__(self, data, start=0, end=None):
        self.data = data
        self.pos = start * 8
        self.end = (len(data) if end is None else end) * 8

    def read(self, count):
        if self.pos + count > self.end:
            self.pos = self.end
            return None
        value = 0
        for _ in range(count):
            value = (value << 1) | ((self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def align(self):
        self.pos = (self.pos + 7) & ~7


def _count(value):
    return 0 if value is None else value


def _descriptor_length(data, pos, end):
    value = 0
    for _ in range(4):
        if pos >= end:
            return None, pos
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    return None, pos


def _es_descriptor_body(data, pos, end):
    """Skip the fixed ES_Descriptor fields, returning the nested descriptor offset."""
    if pos + 3 > end:
        return None
    flags = data[pos + 2]
    pos += 3
    if flags & 0x80:  # streamDependenceFlag
        pos += 2
    if flags & 0x40:  # URL_Flag
        if pos >= end:
            return None
        pos += 1 + data[pos]
    if flags & 0x20:  # OCRstreamFlag
        pos += 2
    return pos if pos <= end else None


def _find_decoder_specific_info(data, pos, end):
    """Walk the nested MPEG-4 descriptors of an ESDS looking for tag 0x05."""
    while pos < end:
        tag = data[pos]
        length, body = _descriptor_length(data, pos + 1, end)
        if length is None or body + length > end:
            return None
        if tag == 0x05:
            return body, body + length
        if tag == 0x03:
            inner = _es_descriptor_body(data, body, body + length)
        elif tag == 0x04:  # DecoderConfigDescriptor
            inner = body + 13
        else:
            inner = None
        if inner is not None and inner < body + length:
            found = _find_decoder_specific_info(data, inner, body + length)
            if found is not None:
                return found
        pos = body + length
    return None


def _rate(bits):
    index = bits.read(4)
    if index is None or index in (13, 14):
        return None
    if index == 15:
        return bits.read(24)
    return RATES[index] if index < len(RATES) else None


def _program_config_element(bits):
    bits.read(4)   # element_instance_tag
    bits.read(2)   # object_type
    bits.read(4)   # sampling_frequency_index
    front = _count(bits.read(4))
    side = _count(bits.read(4))
    back = _count(bits.read(4))
    lfe = _count(bits.read(2))
    assoc = _count(bits.read(3))
    valid_cc = _count(bits.read(4))
    if bits.read(1):  # mono_mixdown_present
        bits.read(4)
    if bits.read(1):  # stereo_mixdown_present
        bits.read(4)
    if bits.read(1):  # matrix_mixdown_idx_present
        bits.read(2)
        bits.read(1)
    for count in (front, side, back):
        for _ in range(count):
            bits.read(1)  # is_cpe
            bits.read(4)  # tag_select
    for _ in range(lfe + assoc):
        bits.read(4)
    for _ in range(valid_cc):
        bits.read(1)
        bits.read(4)
    bits.align()
    for _ in range(_count(bits.read(8))):  # comment_field_bytes
        bits.read(8)


def _ga_specific_config(bits, object_type, channel_config):
    bits.read(1)  # frameLengthFlag
    if bits.read(1):  # dependsOnCoreCoder
        bits.read(14)  # coreCoderDelay
    extension = bits.read(1)
    if channel_config == 0:
        _program_config_element(bits)
    if object_type in (6, 20):
        bits.read(3)  # layerNr
    if extension:
        if object_type == 22:
            bits.read(5)
            bits.read(11)
        if object_type in (17, 19, 20, 23):
            bits.read(3)
        bits.read(1)  # extensionFlag3


def _audio_object_type(bits):
    value = bits.read(5)
    if value == 31:
        extra = bits.read(6)
        return None if extra is None else 32 + extra
    return value


def _audio_specific_config(data, start, end):
    """Parse an MPEG-4 AudioSpecificConfig (ISO/IEC 14496-3, 1.6.2.1)."""
    bits = _Bits(data, start, end)
    object_type = _audio_object_type(bits)
    if object_type is None:
        return None
    sample_rate = _rate(bits)
    channel_config = bits.read(4)
    sbr = ps = False
    output_rate = sample_rate
    explicit = object_type in (5, 29)
    if explicit:  # explicit hierarchical SBR/PS signalling
        sbr = True
        ps = object_type == 29
        extension_rate = _rate(bits)  # the output rate after SBR
        if extension_rate is not None:
            output_rate = extension_rate
        object_type = _audio_object_type(bits)
        if object_type == 22:
            bits.read(4)  # extensionChannelConfiguration
        if object_type is not None:
            _ga_specific_config(bits, object_type, channel_config)
    elif object_type is not None:
        _ga_specific_config(bits, object_type, channel_config)
    # Backwards compatible SBR/PS signalling may follow a plain AAC config.
    if not explicit and bits.read(11) == 0x2B7:
        if bits.read(5) == 5 and bits.read(1):
            sbr = True
            extension_rate = _rate(bits)
            if extension_rate is not None:
                output_rate = extension_rate
            if bits.read(11) == 0x548 and bits.read(1):
                ps = True
    return {"object_type": object_type, "sbr": sbr, "ps": ps, "sample_rate": sample_rate,
            "output_sample_rate": output_rate, "channel_config": channel_config}


def _valid_config(config):
    return (config is not None and config["sample_rate"] is not None
            and config["object_type"] in (1, 2, 3, 4, 6, 7, 17, 19, 20, 21, 22, 23)
            and (config["channel_config"] is None or config["channel_config"] <= 15))


def _cookie(data, start, end):
    """AAC magic cookie: an ESDS descriptor chain, or a bare AudioSpecificConfig.

    Writers differ: some store the raw ESDS, some a bare AudioSpecificConfig,
    and some prefix the cookie with a length or a version/flags word.  Prefer
    the exact layouts, then look for a descriptor after a short prefix, and
    finally a bare config at the first offset that decodes to a real AAC setup.
    """
    if start >= end:
        return None

    def descriptors(pos):
        found = _find_decoder_specific_info(data, pos, end)
        if found is not None and found[0] < found[1]:
            config = _audio_specific_config(data, found[0], found[1])
            if _valid_config(config):
                return config
        return None

    def bare(pos):
        config = _audio_specific_config(data, pos, end)
        return config if _valid_config(config) else None

    if data[start] in (0x03, 0x04):
        config = descriptors(start)
        if config is not None:
            return config
    config = bare(start)
    if config is not None:
        return config
    for pos in range(start + 1, min(end, start + 16)):
        if data[pos] in (0x03, 0x04):
            config = descriptors(pos)
            if config is not None:
                return config
    for pos in range(start + 1, min(end, start + 9)):
        config = bare(pos)
        if config is not None:
            return config
    return None


def _packet_table(data, body, size, bytes_per_packet, frames_per_packet):
    if size < PAKT_HEADER_SIZE:
        return None
    packets, valid, priming, remainder = struct.unpack_from(">qqii", data, body)
    sizes, frames = [], []
    pos, end = body + PAKT_HEADER_SIZE, body + size
    # A packet count that was never finalised (a recording cut short) is
    # recovered from the entries that are actually present.
    count = packets if packets > 0 else None
    read = 0
    if bytes_per_packet == 0 or frames_per_packet == 0:
        while (count is None or read < count) and pos < end:
            if bytes_per_packet == 0:
                value, pos = varint(data, pos, end)
                if value is None:
                    break
                sizes.append(value)
            if frames_per_packet == 0:
                value, pos = varint(data, pos, end)
                if value is None:
                    break
                frames.append(value)
            read += 1
        packets = read if count is None else min(count, read)
    return {"packets": packets, "valid": valid, "priming": priming, "remainder": remainder,
            "sizes": sizes, "frames": frames}


def _info_strings(data, body, size):
    out = {}
    end = body + size
    if size < 4:
        return out
    entries, = struct.unpack_from(">I", data, body)
    pos, parsed = body + 4, 0
    while pos < end and parsed < entries:
        key_end = data.find(b"\0", pos, end)
        if key_end < 0:
            break
        key = data[pos:key_end]
        value_pos = key_end + 1
        if value_pos >= end:
            break
        value_end = data.find(b"\0", value_pos, end)
        if value_end < 0:
            value_end = end  # tolerate a missing final terminator
        out[key.decode("utf-8", "replace")] = data[value_pos:value_end].decode("utf-8", "replace")
        pos, parsed = value_end + 1, parsed + 1
    return out


def _seconds(frames, rate):
    if not math.isfinite(rate) or rate <= 0:
        return None
    value = Fraction(frames) / Fraction(rate)
    if value.denominator > 10 ** 6:  # a float sample rate with binary noise
        value = value.limit_denominator(10 ** 6)
    return str(value)


def parse(data):
    found = chunks(data)
    if not found or found[0][0] != "desc":
        raise CafError("the first chunk must be desc")
    _, body, size = found[0]
    if size < DESC_SIZE:
        raise CafError("the desc chunk is too short")
    rate, fmt, flags, bpp, fpp, ch, bits = struct.unpack_from(">d4sIIIII", data, body)
    name = fmt.decode("latin-1").strip("\0 ")
    out = {"sample_rate": rate, "format": name, "channels": ch, "bits": bits,
           "bytes_per_packet": bpp, "frames_per_packet": fpp, "float": bool(flags & 1) if name == "lpcm" else None,
           "little_endian": bool(flags & 2) if name == "lpcm" else None,
           "chunks": [c for c, _, _ in found], "edit_count": None, "data_bytes": None,
           "packets": None, "frames": None, "priming": 0, "remainder": 0, "duration": None,
           "max_packet_bytes": bpp or None, "max_packet_frames": fpp or None,
           "info": {}, "channel_layout": None, "aac": None}
    pakt = None
    for ctype, body, size in found:
        if ctype == "data":
            if out["data_bytes"] is None:
                if size >= 4:
                    out["edit_count"], = struct.unpack_from(">I", data, body)
                    out["data_bytes"] = size - 4  # the edit count is not audio
                else:
                    out["data_bytes"] = 0
        elif ctype == "pakt" and pakt is None:
            pakt = _packet_table(data, body, size, bpp, fpp)
        elif ctype == "info":
            out["info"] = _info_strings(data, body, size)
        elif ctype == "kuki" and name[:3].lower() == "aac":
            out["aac"] = _cookie(data, body, body + size)
        elif ctype == "chan" and size >= 8:
            tag, bitmap = struct.unpack_from(">II", data, body)
            out["channel_layout"] = {"tag": tag, "bitmap": bitmap}

    if pakt is not None:
        packets = max(pakt["packets"], 0)
        sizes, frames = pakt["sizes"], pakt["frames"]
        priming, remainder = max(pakt["priming"], 0), max(pakt["remainder"], 0)
        if packets or sizes or frames:
            out["packets"] = packets
            out["priming"], out["remainder"] = priming, remainder
            if sizes:
                out["max_packet_bytes"] = max(sizes)
            if frames:
                out["max_packet_frames"] = max(frames)
            total = None
            if fpp:
                total = packets * fpp
            elif frames and len(frames) >= packets:
                total = sum(frames[:packets])
            if total is not None:
                # Priming and remainder frames are encoder padding around the
                # playable audio, so they are not part of the duration.
                total = max(total - priming - remainder, 0)
                out["frames"] = min(pakt["valid"], total) if pakt["valid"] > 0 else total
            elif pakt["valid"] > 0:
                out["frames"] = pakt["valid"]
        else:
            pakt = None  # an empty table carries no information
    if pakt is None and bpp and out["data_bytes"] is not None:
        out["packets"] = out["data_bytes"] // bpp
        if fpp:
            out["frames"] = out["packets"] * fpp

    if out["frames"] is not None:
        out["duration"] = _seconds(out["frames"], rate)
    return out
