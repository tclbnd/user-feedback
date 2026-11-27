"""Bethesda VID movies (.vid): walk the blocks, decode the video frames and the 8-bit sound."""
import zlib

PAL, FIRST_AUDIO, AUDIO, IFRAME, PFRAME, YOFF, EOF = 0x02, 0x7C, 0x7D, 0x03, 0x01, 0x04, 0x14
KIND = {IFRAME: "I", PFRAME: "P", YOFF: "Y"}


class VidError(ValueError):
    pass


class Reader:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def u8(self):
        if self.p >= len(self.d):
            raise EOFError
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        return self.u8() | self.u8() << 8

    def take(self, n):
        if self.p + n > len(self.d):
            raise EOFError
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def u16peek(self):
        if self.p + 2 > len(self.d):
            raise EOFError
        return self.d[self.p] | self.d[self.p + 1] << 8


def _scale6(v):
    """Expand a 6-bit VGA DAC component to 8 bits the way the hardware does:
    the top bits are copied into the low bits so 63 maps to 255, not 252."""
    return (v << 2) | (v >> 4)


def palette(raw):
    out = []
    for k in range(256):
        r, g, b = raw[3 * k], raw[3 * k + 1], raw[3 * k + 2]
        out.append((_scale6(r), _scale6(g), _scale6(b)))
    return out


def video_packet(r, kind, width, npixels):
    """Collect one video block the way the demuxer does; returns the packet bytes after the block type.

    A block ends either on an explicit zero code or as soon as the whole
    picture (or, for a Y offset update, everything below the starting line)
    has been covered -- the last block of a movie often has no zero code.
    An I-frame RLE fill is clipped to the end of its row, so its nominal
    length can overshoot the pixels it actually paints.
    """
    pkt = bytearray()
    dst = 0
    if kind == YOFF:
        pkt += r.take(2)
    while dst < npixels:
        code = r.u8()
        pkt.append(code)
        if code == 0:
            break
        n = code & 0x7F
        if code >= 0x80:
            if kind == IFRAME:
                # One colour byte; the fill never crosses a row boundary.
                pkt.append(r.u8())
                dst += min(n, width - dst % width)
            else:
                # A skip on a P / Y update covers n destination pixels.
                dst += n
        else:
            # A literal run is one byte per pixel and may span rows.
            pkt += r.take(n)
            dst += n
    return bytes(pkt)


def decode(pic, w, h, kind, pkt):
    p = 0
    dst = 0
    end = w * h
    if kind == YOFF:
        y = pkt[0] | pkt[1] << 8
        p = 2
        if y >= h:
            raise VidError("y offset below the picture")
        dst = y * w
    while p < len(pkt):
        code = pkt[p]
        p += 1
        if not code:
            break
        n = code & 0x7F
        if code < 0x80:
            if dst + n > end:
                raise VidError("video packet runs past the picture")
            pic[dst:dst + n] = pkt[p:p + n]
            p += n
            dst += n
        elif kind == IFRAME:
            # An RLE fill paints one colour but is clipped at the row end;
            # any nominal remainder is simply discarded.
            run = min(n, w - dst % w)
            if dst + run > end:
                raise VidError("video packet runs past the picture")
            pic[dst:dst + run] = bytes([pkt[p]]) * run
            p += 1
            dst += run
        else:
            # A P / Y high-bit code is a skip: the destination pixels keep
            # whatever a previous frame left there.
            if dst + n > end:
                raise VidError("video packet runs past the picture")
            dst += n


def parse(data):
    if len(data) < 15 or data[:3] != b"VID":
        raise VidError("not a Bethesda VID file")
    r = Reader(data, 5)
    nframes, w, h, delay = r.u16(), r.u16(), r.u16(), r.u16()
    r.u16()
    if not w or not h:
        raise VidError("empty picture")
    pic = bytearray(w * h)
    cur_pal = [(0, 0, 0)] * 256
    rate = 11111
    audio = bytearray()
    frames, palettes = [], 0
    ended = "data"
    try:
        while r.p < len(data):
            t = r.u8()
            if t == PAL:
                cur_pal = palette(r.take(768))
                palettes += 1
            elif t in (FIRST_AUDIO, AUDIO):
                if t == FIRST_AUDIO:
                    # The three bytes are a big-endian 1 MHz timer divisor;
                    # reading only the last byte gives rates 256x too small.
                    divisor = int.from_bytes(r.take(3), "big")
                    rate = 1000000 // max(1, divisor)
                n = r.u16()
                audio += r.take(n)
            elif t in KIND:
                ticks = r.u16() + delay
                if t == YOFF:
                    y = r.u16peek()
                    if y >= h:
                        raise VidError("y offset below the picture")
                    npixels = w * (h - y)
                else:
                    npixels = w * h
                pkt = video_packet(r, t, w, npixels)
                decode(pic, w, h, t, pkt)
                frames.append({"type": KIND[t], "ticks": ticks, "crc32": "%08x" % zlib.crc32(bytes(pic))})
            elif t == EOF:
                ended = "eof"
                break
            else:
                ended = "error"
                break
    except (EOFError, VidError):
        ended = "error"
    rgb = b"".join(bytes(cur_pal[v]) for v in pic)
    snd = None
    if audio:
        snd = {"rate": rate, "samples": len(audio), "peak": max(abs(v - 128) for v in audio),
               "crc32": "%08x" % zlib.crc32(bytes(audio))}
    return {"width": w, "height": h, "frames_declared": nframes, "frames": frames, "palettes": palettes,
            "rgb_crc32": "%08x" % zlib.crc32(rgb), "audio": snd, "ended": ended}
