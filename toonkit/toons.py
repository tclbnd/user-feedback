"""CDToons animation frames from a rip pack: sprite blocks with lifetimes, referenced sprites, palette sprites."""
import struct
import zlib

MAX_SPRITES = 1200


class ToonError(ValueError):
    pass


def be16(b, p):
    return b[p] << 8 | b[p + 1]


def s16(b, p):
    v = be16(b, p)
    return v - 0x10000 if v & 0x8000 else v


def be32(b, p):
    return be16(b, p) << 16 | be16(b, p + 2)


class Toons:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.pix = bytearray(w * h)
        self.pal = [(0, 0, 0)] * 256
        self.last_pal = 0
        self.sprites = {}

    def render(self, data, dst_x, dst_y, width, height):
        """Returns True when the data ran out."""
        nxt, end = 0, len(data)
        skip = 0
        dst_x, dst_y = max(dst_x, 0), max(dst_y, 0)
        if dst_y + height > self.h:
            height = self.h - dst_y
        for y in range(height):
            p = nxt
            if end - p < 2:
                return True
            line = be16(data, p)
            p += 2
            if end - p < line:
                return True
            nxt = p + line
            if dst_y + y < 0:
                continue
            row = (dst_y + y) * self.w + dst_x
            to_skip, x = skip, 0
            while x < width - skip:
                if p >= end:
                    return True
                val = data[p]
                p += 1
                raw = not val & 0x80
                size = (val & 0x7F) if not raw else (val & 0x7F) + 1
                if to_skip >= size:
                    to_skip -= size
                    step = size if raw else 1
                    if nxt - p < step:
                        return True
                    p += step
                    continue
                elif to_skip:
                    size -= to_skip
                    if raw:
                        if nxt - p < to_skip:
                            return True
                        p += to_skip
                    to_skip = 0
                if x + size >= width - skip:
                    size = width - skip - x
                size = min(size, len(self.pix) - row - x)
                if raw:
                    if nxt - p < size:
                        return True
                    for k in range(size):
                        if data[p + k]:
                            self.pix[row + x + k] = data[p + k]
                    p += size
                else:
                    color = data[p] if p < end else 0
                    p += 1
                    self.pix[row + x:row + x + size] = bytes([color]) * size
                x += size
        return False

    def frame(self, buf):
        if len(buf) < 44:
            raise ToonError("frame header is truncated")
        frame_id = be16(buf, 2)
        background = buf[7]
        count = be16(buf, 28)
        offset = be16(buf, 30)
        referenced = buf[34]
        pal_id = be16(buf, 36)
        pal_set = buf[38]
        if offset > len(buf):
            raise ToonError("sprite offset is past the frame")
        p = offset
        for _ in range(count):
            if p + 14 > len(buf):
                raise ToonError("sprite header is truncated")
            sid = be16(buf, p)
            if sid >= MAX_SPRITES:
                raise ToonError("sprite id is too high")
            if sid in self.sprites:
                raise ToonError("duplicate sprite id")
            size = be32(buf, p + 4)
            if size < 14:
                raise ToonError("sprite is too small")
            size -= 14
            end_frame = be16(buf, p + 10)
            p += 14
            if p + size > len(buf):
                raise ToonError("sprite data is truncated")
            self.sprites[sid] = (end_frame, bytes(buf[p:p + size]))
            p += size
        while p < len(buf):
            if p + 8 > len(buf):
                raise ToonError("entry header is truncated")
            size = be32(buf, p + 4)
            p += 8
            if size < 8 or size - 8 > len(buf) - p:
                raise ToonError("entry is truncated")
            p += size - 8
        if offset >= 44:
            q, qend = 44, offset
            for i in range(referenced):
                if q + 10 > qend:
                    raise ToonError("sprite list is truncated")
                sid, top, left, right = be16(buf, q), s16(buf, q + 2), s16(buf, q + 4), s16(buf, q + 8)
                q += 10

                if sid >= MAX_SPRITES:
                    raise ToonError("sprite id is too high")
                if sid not in self.sprites:
                    continue
                data = self.sprites[sid][1]
                if len(data) < 14:
                    continue
                self.render(data[14:], left, top, be16(data, 2), be16(data, 0))
            if pal_id and pal_id != self.last_pal:
                if pal_id >= MAX_SPRITES:
                    raise ToonError("palette id is too high")
                if pal_id in self.sprites:
                    data = self.sprites[pal_id][1]
                    self.last_pal = pal_id
                    n = min(256, len(data) // 6)
                    self.pal[:n] = [(data[6 * k + 1], data[6 * k + 3], data[6 * k + 5]) for k in range(n)]


def parse(data):
    if data[:8] != b"TOONPACK":
        raise ToonError("not a CDToons pack")
    w, h, count = struct.unpack_from(">HHH", data, 8)
    if not w or not h:
        raise ToonError("empty picture")
    t = Toons(w, h)
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ToonError("frame table is truncated")
        n, = struct.unpack_from(">I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise ToonError("frame is truncated")
        try:
            t.frame(buf)
        except ToonError:
            frames.append({"state": "error", "crc32": None})
            continue
        img = b"".join(bytes(t.pal[v]) for v in t.pix)
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img), "sprites": len(t.sprites)})
    return {"width": w, "height": h, "frames": frames}
