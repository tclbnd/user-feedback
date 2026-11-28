"""Kega Game Video (KGV1) frames from a rip pack: RGB555 literals, back references in the frame and copies from the previous frame."""
import struct
import zlib


class KgvError(ValueError):
    pass


def five(v):
    return v << 3


def parse(data):
    if data[:8] != b"KGV1PACK":
        raise KgvError("not a KGV1 pack")
    count, = struct.unpack_from("<H", data, 8)
    pos = 10
    frames = []
    w = h = 0
    cur = None
    offsets = [-1] * 8
    for _ in range(count):
        if pos + 4 > len(data):
            raise KgvError("frame table is truncated")
        n, = struct.unpack_from("<I", data, pos)
        buf = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if len(buf) < n:
            raise KgvError("frame is truncated")
        if n < 2:
            frames.append({"state": "error", "width": w, "height": h, "crc32": None})
            continue
        fw, fh = buf[0] * 8, buf[1] * 8
        if not fw or not fh:
            raise KgvError("empty picture")
        if cur is None:
            cur = [0] * (fw * fh)
        if (fw, fh) != (w, h):
            w, h = fw, fh
            cur = (cur + [0] * (w * h))[:w * h]
        maxcnt = w * h
        out = 0
        i, end = 2, len(buf)
        while out < maxcnt and end - i >= 2:
            code = buf[i] | buf[i + 1] << 8
            i += 2
            if not code & 0x8000:
                cur[out] = code
                out += 1
                continue
            if code & 0x6000 == 0x6000:
                slot = (code >> 10) & 7
                cnt = (code & 0x3FF) + 2
                if end - i < 3:
                    break
                offsets[slot] = buf[i] | buf[i + 1] << 8 | buf[i + 2] << 16
                i += 3
                start = out + offsets[slot]
                cnt = min(cnt, maxcnt - out)
                cur[out:out + cnt] = cur[start:start + cnt]
            else:
                dist = code & 0x1FFF
                if not code & 0x6000:
                    cnt = 2
                elif code & 0x6000 == 0x2000:
                    cnt = 3
                else:
                    if end - i < 1:
                        break
                    cnt = 4 + buf[i]
                    i += 1
                if out < dist or not dist:
                    break
                cnt = min(cnt, maxcnt - out)
                cur[out:out + cnt] = cur[out - dist:out - dist + cnt]
            out += cnt
        img = b"".join(bytes((five(v >> 10 & 31), five(v >> 5 & 31), five(v & 31))) for v in cur)
        frames.append({"state": "decoded", "width": w, "height": h, "crc32": "%08x" % zlib.crc32(img)})
    return {"frames": frames}
