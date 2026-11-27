"""id Software CIN cut-scenes (.cin): demux video and audio chunks, decode the Huffman-coded frames."""
import zlib

TOKENS = 256


class CinError(ValueError):
    pass


def build_tree(counts):
    """Nodes 0-255 are symbols; new nodes are appended. Returns (children, root)."""
    count = list(counts) + [0] * TOKENS
    used = [False] * (TOKENS * 2)
    children = {}
    n = TOKENS

    def smallest():
        best, node = 99999999, -1
        for i in range(n):
            if used[i] or not count[i]:
                continue
            if count[i] <= best:
                best, node = count[i], i
        if node >= 0:
            used[node] = True
        return node

    while True:
        a = smallest()
        if a == -1:
            break
        b = smallest()
        if b == -1:
            break
        children[n] = (a, b)
        count[n] = count[a] + count[b]
        n += 1
    return children, n - 1


def decode_frame(trees, w, h, data):
    out = bytearray(w * h)
    prev = 0
    pos = bit = 0
    v = 0
    for i in range(w * h):
        if i % w == 0:
            prev = 0
        children, root = trees[prev]
        node = root
        while node >= TOKENS:
            if not bit:
                if pos >= len(data):
                    raise CinError("Huffman data ends early")
                v = data[pos]
                pos += 1
                bit = 8
            node = children[node][v >> 7 & 1]
            v = v << 1 & 0xFF
            bit -= 1
        out[i] = node
        prev = node
    return out


def parse(data):
    if len(data) < 20 + 65536:
        raise CinError("file is too short for the header and Huffman tables")
    w, h, rate, bps, ch = (int.from_bytes(data[k:k + 4], "little") for k in range(0, 20, 4))
    if not w or not h or w * h > 1 << 20:
        raise CinError("bad picture size")
    audio = rate > 0
    if audio and (rate < 14 or bps not in (1, 2) or ch not in (1, 2)):
        raise CinError("bad audio parameters")
    tables = data[20:20 + 65536]
    trees = [build_tree(tables[p * 256:(p + 1) * 256]) for p in range(256)]
    if audio:
        a1 = (rate // 14) * bps * ch
        a2 = a1
    pos = 20 + 65536
    palette = [(0, 0, 0)] * 256
    frames, pcm = [], bytearray()
    flip = 0
    ended = "eof"
    while pos < len(data):
        if pos + 4 > len(data):
            break
        cmd = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        changed = cmd == 1
        if changed:
            raw = data[pos:pos + 768]
            if len(raw) != 768:
                raise CinError("palette is truncated")
            pos += 768
            pal = []
            for i in range(256):
                pal.append(tuple(c << 2 & 0xFF for c in raw[3 * i:3 * i + 3]))
            palette = pal
        size = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + size]
        pos += 4 + size
        try:
            idx = decode_frame(trees, w, h, body)
            rgb = b"".join(bytes(palette[i]) for i in idx)
            frames.append({"palette": changed, "crc32": "%08x" % zlib.crc32(rgb), "colours": len(set(idx))})
        except CinError:
            frames.append({"palette": changed, "crc32": None, "colours": None})
        if audio:
            n = a2 if flip else a1
            chunk = data[pos:pos + n]
            pcm += chunk
            pos += len(chunk)
            flip ^= 1
    out = {"width": w, "height": h, "frames": frames, "ended_by": ended, "audio": None}
    if audio:
        frames_n = len(pcm) // (bps * ch)
        if bps == 1:
            samples = [b - 128 for b in pcm[:frames_n * ch]]
        else:
            samples = [int.from_bytes(pcm[k:k + 2], "little", signed=True) for k in range(0, frames_n * ch * 2, 2)]
        out["audio"] = {"rate": rate, "channels": ch, "bits": bps * 8, "samples": frames_n,
                        "peak": max((abs(s) for s in samples), default=0),
                        "crc32": "%08x" % zlib.crc32(b"".join(s.to_bytes(2, "little", signed=True) for s in samples))}
    return out
