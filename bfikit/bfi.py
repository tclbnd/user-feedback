"""Interplay BFI movies (.bfi): palette and the chain-coded 8-bit frames."""
import zlib


class BfiError(ValueError):
    pass


def palette(raw):
    # Stored values are 6-bit VGA DAC levels; the player expands them to
    # 8 bits by replicating the top two bits into the low two bits.
    return [tuple(((raw[3 * k + c] << 2) | (raw[3 * k + c] >> 4)) & 0xFF
                  for c in range(3)) for k in range(len(raw) // 3)]


def decode(pic, pkt):
    """Apply one frame to the persistent picture; returns 'decoded', 'stopped' or raises.

    The frame chunk starts with its unpacked size (u32); the chain-coded
    picture follows.  Each chain begins with one opcode byte whose top two
    bits select the chain and whose low six bits give the inline length in
    pixels.  A zero inline length switches chains 0, 2 and 3 to a 16-bit
    little-endian extended length.  The chains are:

      0 raw pixels copied verbatim from the stream
      1 back copy of pixels already in the persistent picture (inline form:
        length 1..63 plus an 8-bit offset; marker byte 0x40 selects an
        8-bit length followed by a 16-bit offset)
      2 skip: advance without touching the picture, leaving the previous
        frame in place; a zero extended length (80 00 00) ends the frame
      3 two-colour fill: alternate two palette indices for length pixel pairs
    """
    end = len(pic)
    p = 4
    d = 0

    def byte():
        nonlocal p
        if p >= len(pkt):
            raise BfiError("frame data is truncated")
        v = pkt[p]
        p += 1
        return v

    def le16():
        return byte() | byte() << 8

    while d != end:
        b = byte()
        code, length = b >> 6, b & 0x3F
        offset = 0
        if code == 1:
            # Back copy.  The bare marker byte 0x40 selects the extended
            # form: an 8-bit pixel count followed by a 16-bit little-endian
            # offset.  Any other byte carries the inline pixel count
            # (1..63) in its low six bits, followed by a single 8-bit
            # offset.
            if b == 0x40:
                length = byte()
                offset = le16()
            else:
                length = b & 0x3F
                offset = byte()
        elif length == 0:
            # Other chains use the 16-bit extended length when the inline
            # length field is zero.
            length = le16()

        # The extended skip with a zero length (80 00 00) is the frame's
        # end code; the picture may stop short of filling every pixel.
        if code == 2 and length == 0:
            return "decoded"

        # Lengths are pixel counts; only the two-colour fill lays down a
        # pair of bytes per count.
        span = 2 * length if code == 3 else length
        if d + span > end:
            # A chain running past the picture stops the frame; the player
            # drops the rest of it and keeps what was drawn so far.
            return "stopped"

        if code == 0:
            # raw pixels copied straight from the stream
            if length > len(pkt) - p:
                raise BfiError("normal chain runs past the frame data")
            pic[d:d + length] = pkt[p:p + length]
            p += length
            d += length
        elif code == 1:
            # back copy: repeat pixels already in the picture; forward
            # byte-by-byte copies so overlapping runs keep unfolding.
            src = d - offset
            if src < 0:
                raise BfiError("back chain points before the picture")
            for k in range(length):
                pic[d + k] = pic[src + k]
            d += length
        elif code == 2:
            # skip: leave the persistent picture untouched and move on,
            # so static parts of the scene survive from the previous frame.
            d += length
        else:
            # two-colour fill: alternate c1, c2 for every pixel pair.
            c1, c2 = byte(), byte()
            for k in range(length):
                pic[d + 2 * k] = c1
                pic[d + 2 * k + 1] = c2
            d += 2 * length
    return "decoded"


def parse(data):
    if len(data) < 16 or data[:8] != b"BFIMOVIE":
        raise BfiError("not a BFI movie")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    ncol = int.from_bytes(data[12:14], "little")
    count = int.from_bytes(data[14:16], "little")
    if not w or not h:
        raise BfiError("empty picture")
    if ncol > 256:
        raise BfiError("palette is too large")
    pos = 16
    if pos + 3 * ncol > len(data):
        raise BfiError("palette is truncated")
    pal = palette(data[pos:pos + 3 * ncol]) + [(0, 0, 0)] * (256 - ncol)
    pos += 3 * ncol
    pic = bytearray(w * h)
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise BfiError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise BfiError("frame data is truncated")
        pos += 4 + n
        try:
            state = decode(pic, body)
        except BfiError:
            state = "error"
        rgb = b"".join(bytes(pal[v]) for v in pic)
        frames.append({"state": state, "crc32": "%08x" % zlib.crc32(rgb)})
    return {"width": w, "height": h, "colours": ncol, "frames": frames}
