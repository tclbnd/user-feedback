"""ID3v2.2 / 2.3 / 2.4 tag and frame reading.

Implements the tag layouts from the id3.org structure specifications:

* ID3v2.2.0 - 6-byte frame headers, 3-byte frame ids, 3-byte plain sizes
* ID3v2.3.0 - 10-byte frame headers, plain 32-bit frame sizes
* ID3v2.4.0 - 10-byte frame headers, syncsafe frame sizes

Tag-level unsynchronisation, extended headers and the frame format flags
(grouping identity, compression, encryption, frame-level unsynchronisation,
data length indicator) are handled here so the frame decoders only see the
plain frame payload.
"""
import zlib
from collections import namedtuple

Frame = namedtuple("Frame", "id flags body")


class TagError(ValueError):
    pass


def syncsafe(b):
    """Decode a 4-byte syncsafe integer (each byte carries 7 bits)."""
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def deunsynchronise(data):
    """Reverse ID3v2 unsynchronisation: every FF 00 byte pair becomes FF."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if data[i] == 0xFF and i + 1 < n and data[i + 1] == 0x00:
            out.append(0xFF)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


# ID3v2.2 three-letter frame ids mapped to their 2.3/2.4 four-letter names,
# per the ID3v2.2.0 frame appendix. Unknown ids are passed through unchanged.
V22_FRAME_IDS = {
    # text information frames
    "TT1": "TIT1", "TT2": "TIT2", "TT3": "TIT3",
    "TP1": "TPE1", "TP2": "TPE2", "TP3": "TPE3", "TP4": "TPE4",
    "TCM": "TCOM", "TXT": "TEXT", "TLA": "TLAN", "TCO": "TCON",
    "TAL": "TALB", "TPA": "TPOS", "TRK": "TRCK", "TRC": "TSRC",
    "TYE": "TYER", "TDA": "TDAT", "TIM": "TIME", "TRD": "TRDA",
    "TMT": "TMED", "TST": "TOAL", "TPI": "TOPE", "TPB": "TPUB",
    "TEN": "TORY", "TCR": "TCOP", "TBP": "TBPM", "TFT": "TFLT",
    "TKE": "TKEY", "TLE": "TLEN", "TOF": "TOFN", "TSI": "TSIZ",
    "TDN": "TDLY", "TSS": "TSSE", "TXX": "TXXX", "IPL": "IPLS",
    # URL link frames
    "W00": "WCOM", "W01": "WCOP", "W02": "WOAF", "W03": "WOAR",
    "W04": "WOAS", "W05": "WORS", "W06": "WPAY", "W07": "WPUB",
    "WXX": "WXXX",
    # other frames
    "COM": "COMM", "ULT": "USLT", "CNT": "PCNT", "POP": "POPM",
    "PIC": "APIC", "GEO": "GEOB", "MCI": "MCDI", "MLL": "MLLT",
    "STC": "SYTC", "SLT": "SYLT", "RVA": "RVAD", "ETC": "ETCO",
    "EQU": "EQU2", "REV": "RVRB",
}

_UNSYNC_FLAG = 0x80           # tag header flag, all three versions
_EXT_HEADER_FLAG = 0x40       # 2.3 / 2.4 only
_V22_COMPRESSION_FLAG = 0x40  # 2.2 tag-level compression (no ext header)


def _skip_extended_header(body, major):
    """Return the tag body positioned after the extended header."""
    if len(body) < 4:
        raise TagError("truncated extended header")
    if major == 4:
        # The syncsafe size counts the four size bytes themselves.
        size = syncsafe(body[:4])
        return body[size:]
    # 2.3: plain big-endian size of the extended header data that follows,
    # i.e. the four size bytes themselves are not included.
    size = int.from_bytes(body[:4], "big")
    return body[4 + size:]


def _decode_v23_frame(flags, body):
    """Strip 2.3 frame format extras; None means the frame must be skipped."""
    format_flags = flags[1]
    if format_flags & 0x40:  # encryption - method byte, payload is encrypted
        return None
    if format_flags & 0x20:  # grouping identity byte
        body = body[1:]
    if format_flags & 0x80:  # zlib compression, 4-byte decompressed size first
        body = zlib.decompress(body[4:])
    return body


def _decode_v24_frame(flags, body):
    """Strip 2.4 frame format extras; None means the frame must be skipped."""
    format_flags = flags[1]
    if format_flags & 0x40:  # grouping identity
        body = body[1:]
    if format_flags & 0x10:  # encryption
        return None
    if format_flags & 0x04:  # data length indicator (syncsafe, 4 bytes)
        body = body[4:]
    if format_flags & 0x08:  # frame-level unsynchronisation
        body = deunsynchronise(body)
    if format_flags & 0x20:  # zlib compression
        body = zlib.decompress(body)
    return body


def read_tag(data):
    if len(data) < 10 or data[:3] != b"ID3":
        raise TagError("no ID3v2 tag")
    major = data[3]
    if major not in (2, 3, 4):
        raise TagError("unsupported ID3v2." + str(major))
    tag_flags = data[5]
    size = syncsafe(data[6:10])
    body = data[10:10 + size]
    if len(body) < size:
        raise TagError("truncated tag body")

    if major == 2 and tag_flags & _V22_COMPRESSION_FLAG:
        raise TagError("compressed ID3v2.2 tags are not supported")
    if tag_flags & _UNSYNC_FLAG:
        body = deunsynchronise(body)
    if major in (3, 4) and tag_flags & _EXT_HEADER_FLAG:
        body = _skip_extended_header(body, major)

    frames = []
    pos = 0
    while pos < len(body):
        if major == 2:
            if pos + 6 > len(body) or body[pos] == 0:
                break
            raw_id = body[pos:pos + 3].decode("latin-1")
            fid = V22_FRAME_IDS.get(raw_id, raw_id)
            fsize = (body[pos + 3] << 16) | (body[pos + 4] << 8) | body[pos + 5]
            fflags = b"\x00\x00"
            header_len = 6
        else:
            if pos + 10 > len(body):
                raise TagError("truncated frame header")
            raw_id = body[pos:pos + 4]
            if raw_id == b"\x00\x00\x00\x00":  # padding
                break
            fid = raw_id.decode("latin-1")
            if major == 4:
                fsize = syncsafe(body[pos + 4:pos + 8])
            else:
                fsize = int.from_bytes(body[pos + 4:pos + 8], "big")
            fflags = body[pos + 8:pos + 10]
            header_len = 10

        end = pos + header_len + fsize
        if end > len(body):
            raise TagError("frame " + fid + " exceeds tag body")
        fbody = body[pos + header_len:end]

        if major == 3:
            fbody = _decode_v23_frame(fflags, fbody)
        elif major == 4:
            fbody = _decode_v24_frame(fflags, fbody)
        if fbody is not None:
            frames.append(Frame(fid, fflags, fbody))
        pos = end

    header = {"version": "2." + str(major), "size": 10 + size, "flags": tag_flags}
    return header, frames
