"""Bethesda VID movies (.vid): walk the blocks, decode the video frames and the 8-bit sound."""
import zlib

PAL, FIRST_AUDIO, AUDIO, IFRAME, PFRAME, YOFF, EOF = 0x02, 0x7C, 0x7D, 0x03, 0x01, 0x04, 0x14
KIND = {IFRAME: "I", PFRAME: "P"}


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


def palette(raw):
    out = []
    for k in range(256):
        v = int.from_bytes(raw[3 * k:3 * k + 3], "big") * 4 & 0xFFFFFF
        out.append((v >> 16, v >> 8 & 0xFF, v & 0xFF))
    return out


def video_packet(r, kind, npixels):
    """Collect one video block the way the demuxer does; returns the packet bytes after the block type."""
    pkt = bytearray()
    if kind == YOFF:
        pkt += r.take(2)
    copied = 0
    while True:
        code = r.u8()
        pkt.append(code)
        if code >= 0x80:
            if kind == IFRAME:
                pkt.append(r.u8())
        elif code:
            pkt += r.take(code)
        if code == 0:
            break
    return bytes(pkt)


def decode(pic, w, h, kind, pkt):
    p = 0
    dst = 0
    if kind == YOFF:
        y = pkt[0] | pkt[1] << 8
        p = 2
        if y >= h:
            raise VidError("y offset below the picture")
        dst = y * w
    end = w * h
    remaining = w
    while p < len(pkt):
        code = pkt[p]
        p += 1
        if not code:
            break
        n = code & 0x7F
        if code < 0x80:
            pic[dst:dst + n] = pkt[p:p + n]
            p += n
        elif kind == IFRAME:
            pic[dst:dst + n] = bytes([pkt[p]]) * n
            p += 1
        else:
            pic[dst:dst + n] = bytes(n)
        pic[:] = pic[:end]
        remaining -= n
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
    pending = None
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
                    r.u16()
                    rate = 1000000 // max(1, r.u8())
                n = r.u16()
                audio += r.take(n)
            elif t in KIND:
                ticks = r.u16()
                pkt = video_packet(r, t, w * h)
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
