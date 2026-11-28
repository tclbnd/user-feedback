"""Discworld II BMV video packets: palette, audio and command skipping, nibble-coded copy / literal / repeat runs."""
import struct
import zlib

W, H = 640, 429


class BmvError(ValueError):
    pass


def decode(src, buf, off):
    """buf holds the picture."""
    n = len(src)
    if n <= 0:
        raise BmvError("no picture data")
    base = 0
    end = base + W * H
    fwd = off <= -W or off >= 0
    if fwd:
        s, d, dend = 0, base, end
    else:
        s, d, dend = n - 1, end - 1, base - 1
    step = 1 if fwd else -1
    saved = 0
    tmplen = n
    mode = 0
    while True:
        shift = 0
        flag = 0
        if not mode or tmplen == 4:
            if s < 0 or s >= n:
                return
            val = src[s]
            two = 1
        else:
            val = saved
            two = 0
        if not val & 0xC:
            while True:
                if shift > 22:
                    raise BmvError("bad code")
                if not two:
                    if s < 0 or s >= n:
                        raise BmvError("picture data ran out")
                    shift += 2
                    val |= src[s] << shift
                    if src[s] & 0xC:
                        break
                two = 0
                shift += 2
                mask = (1 << shift) - 1
                val = ((val >> 2) & ~mask) | (val & mask)
                s += step
                if val & (0xC << shift):
                    flag = 1
                    break
        elif mode:
            flag = tmplen != 4
        if flag:
            tmplen = 4
        else:
            saved = val >> (4 + shift)
            tmplen = 0
            val &= (1 << (shift + 4)) - 1
            s += step
        adv = val & 1
        ln = (val >> 1) - 1
        mode += 1 + adv
        if mode >= 4:
            mode -= 3
        if ln <= 0:
            raise BmvError("bad run")
        ln = min(ln, abs(dend - d))
        if mode == 1:
            if fwd:
                buf[d:d + ln] = buf[d + off:d + off + ln]
                d += ln
            else:
                d -= ln
                p = d - base
                if p + W < off or p + W + off < 0 or end - d < off + ln or end - d < ln:
                    raise BmvError("copy goes outside the picture")
                for i in range(ln - 1, -1, -1):
                    buf[d + i] = buf[d + off + i]
        elif mode == 2:
            if fwd:
                buf[d:d + ln] = src[s:s + ln].ljust(ln, b"\0")
                d += ln
                s += ln
            else:
                if s < ln:
                    raise BmvError("literal data ran out")
                d -= ln
                s -= ln
                buf[d:d + ln] = src[s:s + ln]
        else:
            v = src[s] if 0 <= s < n else 0
            s += step
            if fwd:
                buf[d:d + ln] = bytes([v]) * ln
                d += ln
            else:
                d -= ln
                buf[d:d + ln] = bytes([v]) * ln
        if d == dend:
            return


def parse(data):
    if data[:8] != b"BMVPACK1":
        raise BmvError("not a BMV pack")
    w, h, count = struct.unpack_from("<HHH", data, 8)
    if (w, h) != (W, H):
        raise BmvError("BMV video is always 640 x 429")
    buf = bytearray(W * H)
    pal = [0] * 256
    pos = 14
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise BmvError("packet table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        pkt = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(pkt) < n or n < 1:
            raise BmvError("packet is truncated")
        try:
            t = pkt[0]
            p = 1
            if t & 0x20:
                blobs = pkt[1] if n > 1 else 0
                p += 1 + blobs * 64
            if t & 0x10:
                cs = 10
                if p + cs > n:
                    raise BmvError("command does not fit")
                p += cs
            if t & 0x08:
                if p > n - 768:
                    raise BmvError("palette does not fit")
                pal = [pkt[p + 3 * k + 2] << 16 | pkt[p + 3 * k + 1] << 8 | pkt[p + 3 * k] for k in range(256)]
                p += 768
            if t & 0x04:
                if p > n - 2:
                    raise BmvError("scroll offset does not fit")
                off = struct.unpack_from("<h", pkt, p)[0]
                p += 2
            else:
                off = 0
            decode(pkt[p:], buf, off)
        except BmvError:
            frames.append({"state": "error", "crc32": None})
            continue
        pic = bytes(buf)
        img = bytearray(3 * len(pic))
        for k, sh in enumerate((16, 8, 0)):
            img[k::3] = pic.translate(bytes(pal[c] >> sh & 0xFF for c in range(256)))
        img = bytes(img)
        frames.append({"state": "decoded", "crc32": "%08x" % zlib.crc32(img)})
    return {"width": W, "height": H, "frames": frames}
