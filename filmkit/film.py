"""Sega FILM / CPK containers (.cpk): stream descriptions, sample table and planar PCM audio."""
import zlib


class FilmError(ValueError):
    pass


def be32(b, o):
    return int.from_bytes(b[o:o + 4], "big")


def parse(data):
    if len(data) < 16 or data[:4] != b"FILM":
        raise FilmError("not a Sega FILM file")
    data_offset = be32(data, 4)
    version = be32(data, 8)
    pos = 16
    if True:
        fd = data[pos:pos + 32]
        pos += 32
        if len(fd) < 32:
            raise FilmError("FDSC chunk is truncated")
        a_rate = int.from_bytes(fd[24:26], "big")
        a_ch, a_bits = fd[21], fd[22]
        if a_ch > 0:
            a_type = {8: "pcm_s8_planar", 16: "pcm_s16be_planar"}.get(a_bits)
        else:
            a_type = None
    if len(fd) < 20 or fd[:4] != b"FDSC":
        raise FilmError("missing FDSC chunk")
    tag = fd[8:12]
    v_type = "cinepak" if tag == b"cvid" else "raw" if tag == b"raw " else None
    if v_type is None and a_type is None:
        raise FilmError("no stream the player can use")
    width, height = be32(fd, 16), be32(fd, 12)
    st = data[pos:pos + 16]
    if len(st) < 16 or st[:4] != b"STAB":
        raise FilmError("missing STAB chunk")
    clock, count = be32(st, 8), be32(st, 12)
    pos += 16
    table = []
    a_pts = 0
    for i in range(count):
        e = data[pos:pos + 16]
        if len(e) < 16:
            raise FilmError("sample table is truncated")
        pos += 16
        off, size, info = be32(e, 0), be32(e, 4), be32(e, 8)
        if info == 0xFFFFFFFF:
            item = {"stream": "audio", "pts": a_pts, "key": True}
            if a_type is not None:
                a_pts += size // a_ch
        else:
            item = {"stream": "video", "pts": info, "key": bool(info >> 31)}
        item.update(offset=off, size=size)
        table.append(item)
    packets, pcm, frames, keys = [], [], 0, 0
    for i, t in enumerate(table):
        nxt = table[i + 1] if i + 1 < len(table) else None
        blob = data[t["offset"]:t["offset"] + t["size"]]
        ok = len(blob) == t["size"]
        packets.append({"stream": t["stream"], "pts": t["pts"], "duration": nxt["pts"] - t["pts"] if nxt else None,
                        "key": t["key"], "size": t["size"], "ok": ok})
        if t["stream"] == "video":
            frames += 1
            keys += t["key"]
        elif ok and a_type in ("pcm_s8_planar", "pcm_s16be_planar"):
            bps = a_bits // 8
            for k in range(0, t["size"] - bps + 1, bps):
                if bps == 1:
                    pcm.append((blob[k] - 128) << 8)
                else:
                    pcm.append(int.from_bytes(blob[k:k + 2], "big", signed=True))
    out = {"version": version, "base_clock": clock, "packets": packets,
           "video": {"codec": v_type, "width": width, "height": height, "frames": frames, "keyframes": keys} if v_type else None,
           "audio": None}
    if a_type:
        out["audio"] = {"codec": a_type, "rate": a_rate, "channels": a_ch, "bits": a_bits, "samples": a_pts}
        if a_type != "adx":
            out["audio"].update(peak=max((abs(v) for v in pcm), default=0),
                                crc32="%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little", signed=True) for v in pcm)))
    return out
