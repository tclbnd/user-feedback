"""RIFF WAVE cue points, labels, sampler loops and instrument data."""
import struct
from fractions import Fraction

LOOP_TYPES = {0: "forward", 1: "backward", 2: "alternating"}


class WavError(ValueError):
    pass


def chunks(data, start, end):
    pos = start
    while pos + 8 <= end:
        cid, size = struct.unpack_from("<4sI", data, pos)
        body = pos + 8
        if body + size > end:
            raise WavError("chunk %r runs past its parent" % cid)
        yield cid.decode("latin-1"), body, body + size
        pos = body + size


def zstr(b):
    return b.split(b"\0", 1)[0].decode("latin-1")


def parse(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a RIFF WAVE file")
    end = len(data)
    fmt = None
    frames = None
    cues, labels, notes, spans, loops = {}, {}, {}, {}, []
    sampler = inst = None
    info = {}
    playlist = []
    for cid, s, e in chunks(data, 12, end):
        if cid == "fmt ":
            tag, ch, rate, _, align, bits = struct.unpack_from("<HHIIHH", data, s)
            fmt = {"channels": ch, "sample_rate": rate, "block_align": align, "bits": bits}
        elif cid == "data":
            if fmt is None:
                raise WavError("data before fmt")
            frames = (e - s) // fmt["block_align"]
        elif cid == "cue ":
            (n,) = struct.unpack_from("<I", data, s)
            for i in range(n):
                cue_id, position, chunk_id, chunk_start, block_start, offset = struct.unpack_from("<II4sIII", data, s + 4 + 24 * i)
                cues[cue_id] = {"id": cue_id, "sample": position}
        elif cid == "LIST":
            for sid, ss, se in chunks(data, s + 4, e):
                (cue_id,) = struct.unpack_from("<I", data, ss)
                if sid in ("labl", "note"):
                    labels[cue_id] = zstr(data[ss + 4:se])
        elif cid == "smpl":
            (manuf, product, period, note, pitch_frac, smpte_fmt, smpte_off, n, extra) = struct.unpack_from("<9I", data, s)
            sampler = {"manufacturer": "%02X" % manuf if manuf else None, "product": product, "midi_note": note,
                       "pitch_fraction": str(Fraction(pitch_frac, 100)), "sample_period_ns": period,
                       "smpte_offset": "%02d:%02d:%02d:%02d" % tuple(smpte_off.to_bytes(4, "big")) if smpte_fmt else None}
            for i in range(n):
                cue_id, ltype, start, stop, frac, count = struct.unpack_from("<6I", data, s + 36 + 24 * i)
                loops.append({"cue": cue_id, "type": LOOP_TYPES.get(ltype, "vendor"), "start": start, "end": stop,
                              "end_fraction": str(frac), "length": stop - start, "play_count": count})
        elif cid == "inst":
            note, detune, gain, lo_note, hi_note, lo_vel, hi_vel = struct.unpack_from("<BBBBBBB", data, s)
            inst = {"note": note, "detune_cents": detune, "gain_db": gain, "notes": [lo_note, hi_note], "velocities": [lo_vel, hi_vel]}
    if fmt is None or frames is None:
        raise WavError("missing fmt or data chunk")
    markers = []
    for cue_id in cues:
        c = cues[cue_id]
        m = {"id": cue_id, "sample": c["sample"], "time": str(Fraction(c["sample"], fmt["sample_rate"])),
             "label": labels.get(cue_id), "note": notes.get(cue_id)}
        if cue_id in spans:
            m["length"] = spans[cue_id]
        markers.append(m)
    return {**fmt, "frames": frames, "markers": markers, "playlist": playlist, "sampler": sampler,
            "loops": loops, "instrument": inst, "info": info}
