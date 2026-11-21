"""RTP payloads from the media gateway, turned into AMR storage files.

Both RFC 4867 payload formats are handled, without interleaving and without
payload CRC: octet-aligned (section 4.4) and bandwidth-efficient (section
4.3).  The result is the section 6 file storage format.  The two payload
formats carry the same speech bits, so a call leg produces the same storage
file whichever format the session negotiated.
"""
from .frames import CODECS, AmrError, frame_bits

MAGIC = {name: spec["magic"] for name, spec in CODECS.items()}

# Payload header: a 4-bit CMR field, plus 4 reserved bits in octet-aligned
# payloads so that the header and the ToC entries are octet-aligned.
HEADER_BITS = {"octet-aligned": 8, "bandwidth-efficient": 4}
# ToC entry: F, FT, Q and (octet-aligned only) two padding-count bits.
TOC_BITS = {"octet-aligned": 8, "bandwidth-efficient": 6}


class _Bits:
    """MSB-first bit reader over a bytes object."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.end = len(data) * 8

    def left(self):
        return self.end - self.pos

    def skip(self, count):
        if count > self.left():
            raise AmrError("truncated RTP payload")
        self.pos += count

    def read(self, count):
        if count > self.left():
            raise AmrError("truncated RTP payload")
        value = 0
        for index in range(self.pos, self.pos + count):
            byte = self.data[index >> 3]
            value = (value << 1) | ((byte >> (7 - (index & 7))) & 1)
        self.pos += count
        return value


def _toc_entries(bits, width):
    """ToC entries until one has F=0; F is the entry's most significant bit."""
    entries = []
    while True:
        entry = bits.read(width)
        entries.append(entry)
        if not (entry >> (width - 1)) & 1:
            return entries


def _frame_entry(entry, mode):
    if mode == "octet-aligned":
        # F, FT, Q and P.  P counts extra padding bits at the very end of the
        # payload; a frame's own speech bits are padded out to whole octets
        # in a way that is fixed by its frame type, not by P.
        return (entry >> 3) & 0x0F, bool(entry & 0x04)
    # F, FT and Q; the speech bits follow with no octet alignment.
    return (entry >> 1) & 0x0F, bool(entry & 0x01)


def _octet_aligned(bits, codec):
    """Each frame takes ceil(speech bits / 8) octets, starting on an octet."""
    for entry in _toc_entries(bits, TOC_BITS["octet-aligned"]):
        ft, quality = _frame_entry(entry, "octet-aligned")
        nbits = frame_bits(ft, codec)
        speech = bits.read(nbits)
        bits.skip(-nbits % 8)  # pad the frame to the next octet boundary
        yield ft, quality, speech, nbits
    # Anything after the last frame is payload padding, not a frame: there
    # is nothing more to read.


def _bandwidth_efficient(bits, codec):
    """Speech bits of successive frames are packed with no octet alignment."""
    for entry in _toc_entries(bits, TOC_BITS["bandwidth-efficient"]):
        ft, quality = _frame_entry(entry, "bandwidth-efficient")
        nbits = frame_bits(ft, codec)
        yield ft, quality, bits.read(nbits), nbits


_DEPACKETIZERS = {
    "octet-aligned": _octet_aligned,
    "bandwidth-efficient": _bandwidth_efficient,
}


def _storage_frame(ft, quality, speech, nbits):
    """Storage frame: header octet (P=0, FT, Q, R=0) then the speech bits."""
    header = (ft << 3) | (int(quality) << 2)
    return bytes([header]) + (speech << (-nbits % 8)).to_bytes(
        (nbits + 7) // 8, "big")


def depacketize(payloads, codec="amr-nb", mode="octet-aligned"):
    """payloads: list of RTP payloads (bytes), in sequence order."""
    if codec not in MAGIC:
        raise AmrError(f"unknown codec {codec}")
    if mode not in _DEPACKETIZERS:
        raise AmrError(f"unknown payload format {mode}")

    out = [MAGIC[codec]]
    for payload in payloads:
        bits = _Bits(payload)
        if bits.left() < HEADER_BITS[mode] + TOC_BITS[mode]:
            # A payload carrying only a codec mode request: it says nothing
            # about the message itself.
            continue
        bits.skip(HEADER_BITS[mode])  # CMR, and the reserved bits
        for ft, quality, speech, nbits in _DEPACKETIZERS[mode](bits, codec):
            out.append(_storage_frame(ft, quality, speech, nbits))
    return b"".join(out)
