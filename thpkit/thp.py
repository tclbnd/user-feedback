"""Nintendo THP movies (GameCube / Wii): header, components, chained frames."""
import struct

VIDEO, AUDIO, NONE = 0, 1, 0xFF


class ThpError(ValueError):
    pass


def parse(data):
    if data[:4] != b"THP\0":
        raise ThpError("not a THP movie")
    version, max_buf, max_audio = struct.unpack_from(">III", data, 4)
    fps, = struct.unpack_from(">I", data, 16)
    frames, first_size, data_size, comp_off, offsets_off, first_off, last_off = struct.unpack_from(">7I", data, 20)
    v11 = version >= 0x00011000
    out = {"version": "%d.%d" % (version >> 16, version & 0xFFFF), "fps": "%.3f" % fps, "frames": frames,
           "duration": "%.3f" % (frames / fps) if fps > 0 else None, "video": None, "audio": None,
           "frames_walked": 0, "video_bytes": 0, "audio_samples": 0, "largest_frame": 0, "truncated": False}
    count, = struct.unpack_from(">I", data, comp_off)
    kinds = [VIDEO, AUDIO][:count]
    p = comp_off + 20
    comps = []
    for kind in kinds[:count]:
        if kind == VIDEO:
            w, h = struct.unpack_from(">II", data, p)
            out["video"] = {"width": w, "height": h}
            p += 8
        elif kind == AUDIO:
            ch, rate, samples = struct.unpack_from(">III", data, p)
            out["audio"] = {"channels": ch, "sample_rate": rate, "samples": samples}
            p += 12
        elif kind == NONE:
            break
        comps.append(kind)
    pos, size = first_off, first_size
    for _ in range(frames):
        if pos + size > len(data):
            out["truncated"] = True
            break
        next_size, _prev = struct.unpack_from(">II", data, pos)
        q = pos + 8
        sizes = struct.unpack_from(">2I", data, q)
        q += 8
        for kind, n in zip(comps, sizes):
            if kind == VIDEO:
                out["video_bytes"] += n
            elif kind == AUDIO:
                out["audio_samples"] += struct.unpack_from(">I", data, q + 4)[0]   # channel size, then samples
            q += n
        out["frames_walked"] += 1
        out["largest_frame"] = max(out["largest_frame"], size)
        pos = pos + size
    return out
