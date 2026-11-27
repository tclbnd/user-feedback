"""Sega FILM / CPK containers (.cpk): stream descriptions, sample table and planar PCM audio."""
import zlib


class FilmError(ValueError):
    pass


# Every sample table entry whose info word is all ones belongs to the audio
# stream; every other entry is a video packet.
AUDIO_INFO = 0xFFFFFFFF
# On video entries the top bit of the info word is the keyframe flag:
# a clear bit marks a keyframe, a set bit marks a delta frame. The remaining
# 31 bits hold the presentation timestamp in base_clock ticks.
KEYFRAME_BIT = 0x80000000
PTS_MASK = 0x7FFFFFFF


def be32(b, o):
    return int.from_bytes(b[o:o + 4], "big")


def parse(data):
    if len(data) < 16 or data[:4] != b"FILM":
        raise FilmError("not a Sega FILM file")
    data_offset = be32(data, 4)
    version = be32(data, 8)

    # Walk the chunks by their tag/length header instead of assuming a fixed
    # FDSC size: old (version 0) files carry a 20-byte FDSC holding only the
    # video descriptor, while Saturn-era (version 1) files carry a 32-byte
    # FDSC that also describes the audio stream.
    pos = 16
    v_type = width = height = None
    a_type = a_rate = a_ch = a_bits = None
    clock = count = None
    entry_pos = None
    while pos + 8 <= len(data) and pos < data_offset:
        tag = bytes(data[pos:pos + 4])
        size = be32(data, pos + 4)
        if size < 8 or pos + size > len(data):
            raise FilmError("chunk extends past the file")
        body = data[pos + 8:pos + size]
        if tag == b"FDSC":
            if len(body) < 12:
                raise FilmError("FDSC chunk is truncated")
            fourcc = bytes(body[0:4])
            v_type = ("cinepak" if fourcc == b"cvid"
                      else "raw" if fourcc == b"raw " else None)
            height = be32(body, 4)
            width = be32(body, 8)
            # Audio parameters only exist in the 32-byte version 1 FDSC.
            # Body layout after the 12-byte video descriptor (fourcc, height,
            # width): [12:14] reserved, [13] channels, [14] bits per sample,
            # [15] reserved, [16:18] sample rate (big endian), then padding.
            if len(body) >= 18:
                a_ch = body[13]
                a_bits = body[14]
                a_rate = int.from_bytes(body[16:18], "big")
                if a_ch > 0:
                    a_type = {8: "pcm_s8_planar",
                              16: "pcm_s16be_planar"}.get(a_bits)
        elif tag == b"STAB":
            if len(body) < 8:
                raise FilmError("STAB chunk is truncated")
            clock = be32(body, 0)
            count = be32(body, 4)
            # Records follow the 16-byte STAB header (tag, length, clock,
            # count) in both format versions.
            entry_pos = pos + 16
        pos += size

    if entry_pos is None:
        raise FilmError("missing STAB chunk")
    if v_type is None and a_type is None:
        raise FilmError("no stream the player can use")

    # Read the sample table. Each record is [offset, size, info, pad]; the
    # offsets are relative to the FILM data offset rather than absolute file
    # positions. An all-ones info word marks an audio packet, otherwise it is
    # a video packet whose info word holds the keyframe flag and PTS.
    table = []
    a_pts = 0
    p = entry_pos
    for i in range(count):
        e = data[p:p + 16]
        p += 16
        if len(e) < 16:
            raise FilmError("sample table is truncated")
        # Both versions store a uniform [offset, size, info, pad] record;
        # offsets are relative to the FILM data offset.
        off, size, info = be32(e, 0), be32(e, 4), be32(e, 8)
        if info == AUDIO_INFO:
            item = {"stream": "audio", "pts": a_pts, "key": True}
            if a_type is not None and a_ch and a_bits:
                a_pts += size // (a_ch * (a_bits // 8))
        else:
            item = {"stream": "video",
                    "pts": info & PTS_MASK,
                    "key": (info & KEYFRAME_BIT) == 0}
        item.update(offset=off, size=size)
        table.append(item)

    # Timestamp of the next packet on the same stream, so each packet's
    # duration never borrows the other stream's timeline.
    next_pts = {}
    for t in reversed(table):
        t["duration"] = next_pts.get(t["stream"])
        if t["duration"] is not None:
            t["duration"] -= t["pts"]
        next_pts[t["stream"]] = t["pts"]

    packets, pcm, frames, keys = [], [], 0, 0
    for t in table:
        start = data_offset + t["offset"]
        blob = data[start:start + t["size"]]
        ok = len(blob) == t["size"]
        packets.append({"stream": t["stream"], "pts": t["pts"],
                        "duration": t["duration"], "key": t["key"],
                        "size": t["size"], "ok": ok})
        if t["stream"] == "video":
            frames += 1
            keys += 1 if t["key"] else 0
        elif ok and a_type in ("pcm_s8_planar", "pcm_s16be_planar"):
            # Each packet holds one plane per channel (all of channel 0, then
            # all of channel 1, ...); de-interleave into L/R sample frames.
            bps = a_bits // 8
            nframes = t["size"] // (a_ch * bps)
            planes = [blob[c * nframes * bps:(c + 1) * nframes * bps]
                      for c in range(a_ch)]
            for f in range(nframes):
                for c in range(a_ch):
                    q = f * bps
                    if bps == 1:
                        # Stored as signed 8-bit; widen to 16-bit.
                        sample = int.from_bytes(planes[c][q:q + 1],
                                                "big", signed=True) << 8
                    else:
                        sample = int.from_bytes(planes[c][q:q + 2],
                                                "big", signed=True)
                    pcm.append(sample)

    out = {"version": version, "base_clock": clock, "packets": packets,
           "video": {"codec": v_type, "width": width, "height": height,
                     "frames": frames, "keyframes": keys} if v_type else None,
           "audio": None}
    if a_type:
        out["audio"] = {"codec": a_type, "rate": a_rate, "channels": a_ch,
                        "bits": a_bits, "samples": a_pts}
        if a_type != "adx":
            out["audio"].update(
                peak=max((abs(v) for v in pcm), default=0),
                crc32="%08x" % zlib.crc32(
                    b"".join(v.to_bytes(2, "little", signed=True)
                             for v in pcm)))
    return out
