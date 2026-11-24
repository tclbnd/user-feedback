"""Nintendo THP movies (GameCube / Wii): header, components, chained frames."""
import struct

VIDEO, AUDIO, NONE = 0, 1, 0xFF


class ThpError(ValueError):
    pass


def parse(data):
    if data[:4] != b"THP\0":
        raise ThpError("not a THP movie")
    version, max_buf, max_audio = struct.unpack_from(">III", data, 4)
    # The frame rate word is a 32-bit big-endian IEEE float, not an integer.
    fps, = struct.unpack_from(">f", data, 16)
    frames, first_size, data_size, comp_off, offsets_off, first_off, last_off = struct.unpack_from(">7I", data, 20)
    # Version 1.0 is 0x00010000, 1.1 is 0x00011000: the minor number occupies
    # the high nibble of the low half-word (0x1000 -> 1), so shift it down.
    out = {"version": "%d.%d" % (version >> 16, (version & 0xFFFF) >> 12),
           "fps": "%.3f" % fps, "frames": frames,
           "duration": "%.3f" % (frames / fps) if fps > 0 else None, "video": None, "audio": None,
           "frames_walked": 0, "video_bytes": 0, "audio_samples": 0, "largest_frame": 0, "truncated": False}
    # Read the component kinds straight from the component table: audio may be
    # listed before video. The table starts with a 4-byte count, then a 16-byte
    # area holding one kind byte per component (rest 0xFF padding); the packed
    # component records follow at comp_off + 20, in the same order. A video
    # record is 8 bytes (width, height); an audio record is 16 bytes (channels,
    # sample rate, sample count, one trailing word).
    count, = struct.unpack_from(">I", data, comp_off)
    kinds = list(data[comp_off + 4:comp_off + 4 + count])
    p = comp_off + 20
    comps = []
    for kind in kinds:
        if kind == VIDEO:
            w, h = struct.unpack_from(">II", data, p)
            out["video"] = {"width": w, "height": h}
            p += 8
            comps.append(kind)
        elif kind == AUDIO:
            ch, rate, samples = struct.unpack_from(">III", data, p)
            out["audio"] = {"channels": ch, "sample_rate": rate, "samples": samples}
            p += 16
            comps.append(kind)
        elif kind == NONE:
            break
    # Follow the frame chain. Each frame begins with the next frame's size, so
    # the size used for the following step comes from the frame just read.
    pos, size = first_off, first_size
    for _ in range(frames):
        if pos + size > len(data):
            out["truncated"] = True
            break
        next_size, _prev = struct.unpack_from(">II", data, pos)
        q = pos + 8
        sizes = struct.unpack_from(">%dI" % len(comps), data, q)
        # One data size per component, in component-table order.
        q += 4 * len(comps)
        for kind, n in zip(comps, sizes):
            if kind == VIDEO:
                out["video_bytes"] += n
            elif kind == AUDIO and n >= 8:
                # An audio chunk starts with its total size and sample count.
                out["audio_samples"] += struct.unpack_from(">I", data, q + 4)[0]
            q += n
        out["frames_walked"] += 1
        out["largest_frame"] = max(out["largest_frame"], size)
        pos += size
        size = next_size
        if size <= 0:
            # No link to another frame: a truncated file stops the chain here.
            if out["frames_walked"] < frames:
                out["truncated"] = True
            break
    return out
