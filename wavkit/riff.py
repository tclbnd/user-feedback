"""RIFF WAVE cue points, labels, sampler loops and instrument data."""
import struct
from fractions import Fraction

# dwType of a sampler loop.  These three values are defined by the RIFF
# "Multimedia Programming Interface and Data Specifications 1.0"; anything
# else is left to the sampler that wrote it.
LOOP_TYPES = {0: "forward", 1: "alternating", 2: "backward"}

# dwMIDIPitchFraction and a loop's dwFraction count in 1/2**32 of a unit
# (a semitone and a sample respectively), so 0x80000000 is one half.
UNIT = 1 << 32

# dwSMPTEFormat holds the frame rate; 29 stands for 30 fps counted with drop
# frames (29.97 fps).
SMPTE_DROP_FRAME = 29


class WavError(ValueError):
    pass


def chunks(data, start, end):
    """Walk word-aligned chunks in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        cid, size = struct.unpack_from("<4sI", data, pos)
        body = pos + 8
        if body + size > end:
            raise WavError("chunk %r runs past its parent" % cid)
        yield cid.decode("latin-1"), body, body + size
        pos = body + size + (size & 1)  # pad to an even offset


def zstr(b):
    return b.split(b"\0", 1)[0].decode("latin-1")


def frac(value):
    """A fraction of a semitone/sample, counted in 1/2**32 steps."""
    return str(Fraction(value, UNIT))


def cue_sample(position, offset):
    """Frame offset of a cue point inside the data chunk.

    dwSampleOffset is that offset, and the spec expects it to be filled in.
    Some editors instead leave it zero and put the position in dwPosition, and
    a few use dwPosition for a running index, so take dwSampleOffset when it
    is set and fall back to dwPosition.  Reading dwPosition first is what made
    every marker of such a file land on 0 seconds.
    """
    return offset if offset else position


def midi_manufacturer(value):
    """MIDI manufacturer ID as hex bytes ("41", "00 20 33"), or None.

    dwManufacturer is the MIDI System Exclusive manufacturer ID: a one-byte
    code below 0x80, or a three-byte code that starts with the 0x00 escape,
    both read as a plain DWORD number.
    """
    if not value:
        return None
    if value < 0x80:
        return "%02X" % value
    return "%02X %02X %02X" % (value >> 16, (value >> 8) & 0xFF, value & 0xFF)


def smpte(fmt, offset):
    """SMPTE offset as hh:mm:ss:ff, or None when no format is set."""
    if not fmt:
        return None
    hours, minutes, seconds, frames = offset.to_bytes(4, "big")
    sep = ";" if fmt == SMPTE_DROP_FRAME else ":"  # ';' marks drop-frame time
    return "%02d:%02d:%02d%s%02d" % (hours, minutes, seconds, sep, frames)


def parse(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a RIFF WAVE file")
    (riff_size,) = struct.unpack_from("<I", data, 4)
    end = min(len(data), riff_size + 8)  # ignore bytes past the RIFF end
    fmt = None
    frames = None
    cues, labels, notes, spans = {}, {}, {}, {}
    loops = []
    sampler = inst = None
    info = {}
    playlist = []
    for cid, s, e in chunks(data, 12, end):
        if cid == "fmt ":
            if e - s < 16:
                raise WavError("fmt chunk is too short")
            _, ch, rate, _, align, bits = struct.unpack_from("<HHIIHH", data, s)
            if not align:
                raise WavError("fmt chunk has no block alignment")
            fmt = {"channels": ch, "sample_rate": rate, "block_align": align, "bits": bits}
        elif cid == "data":
            if fmt is None:
                raise WavError("data before fmt")
            if frames is None:
                frames = (e - s) // fmt["block_align"]
        elif cid == "cue ":
            if e - s < 4:
                continue
            (n,) = struct.unpack_from("<I", data, s)
            for i in range(min(n, (e - s - 4) // 24)):
                cue_id, position, _, _, _, offset = struct.unpack_from("<II4sIII", data, s + 4 + 24 * i)
                cues[cue_id] = {"id": cue_id, "sample": cue_sample(position, offset)}
        elif cid == "LIST":
            if e - s < 4:
                continue
            kind = data[s:s + 4]
            if kind == b"adtl":
                for sid, ss, se in chunks(data, s + 4, e):
                    if se - ss < 4:
                        continue
                    (cue_id,) = struct.unpack_from("<I", data, ss)
                    if sid == "labl":
                        labels[cue_id] = zstr(data[ss + 4:se])
                    elif sid == "note":
                        notes[cue_id] = zstr(data[ss + 4:se])
                    elif sid == "ltxt" and se - ss >= 20:
                        # dwName, dwSampleLength, fccPurpose, then the
                        # country/language/dialect/codepage DWORDs and text.
                        (length,) = struct.unpack_from("<I", data, ss + 4)
                        spans[cue_id] = length
            elif kind == b"INFO":
                for sid, ss, se in chunks(data, s + 4, e):
                    info[sid] = zstr(data[ss:se])
        elif cid == "plst":
            if e - s < 4:
                continue
            (n,) = struct.unpack_from("<I", data, s)
            for i in range(min(n, (e - s - 4) // 12)):
                cue_id, length, repeats = struct.unpack_from("<III", data, s + 4 + 12 * i)
                playlist.append({"cue": cue_id, "length": length, "repeats": repeats})
        elif cid == "smpl":
            if e - s < 36:
                raise WavError("smpl chunk is too short")
            manuf, product, period, note, pitch, smpte_fmt, smpte_off, n, _ = struct.unpack_from("<9I", data, s)
            sampler = {"manufacturer": midi_manufacturer(manuf), "product": product, "midi_note": note,
                       "pitch_fraction": frac(pitch), "sample_period_ns": period,
                       "smpte_offset": smpte(smpte_fmt, smpte_off)}
            for i in range(min(n, (e - s - 36) // 24)):
                cue_id, ltype, start, stop, fraction, count = struct.unpack_from("<6I", data, s + 36 + 24 * i)
                loops.append({"cue": cue_id, "type": LOOP_TYPES.get(ltype, "vendor"),
                              "start": start, "end": stop, "end_fraction": frac(fraction),
                              # dwStart and dwEnd are both inside the loop, so
                              # one pass covers end - start + 1 frames.
                              "length": stop - start + 1,
                              "play_count": count or None})  # 0 = loop forever
        elif cid == "inst":
            if e - s < 7:
                raise WavError("inst chunk is too short")
            # unShiftedNote, fineTune and gain (both signed), then the note
            # and velocity ranges.
            note, detune, gain, lo_note, hi_note, lo_vel, hi_vel = struct.unpack_from("<BbbBBBB", data, s)
            inst = {"note": note, "detune_cents": detune, "gain_db": gain,
                    "notes": [lo_note, hi_note], "velocities": [lo_vel, hi_vel]}
    if fmt is None or frames is None:
        raise WavError("missing fmt or data chunk")
    markers = []
    for cue_id, c in cues.items():
        m = {"id": cue_id, "sample": c["sample"], "time": str(Fraction(c["sample"], fmt["sample_rate"])),
             "label": labels.get(cue_id), "note": notes.get(cue_id)}
        if cue_id in spans:
            m["length"] = spans[cue_id]
        markers.append(m)
    markers.sort(key=lambda m: (m["sample"], m["id"]))
    return {**fmt, "frames": frames, "markers": markers, "playlist": playlist, "sampler": sampler,
            "loops": loops, "instrument": inst, "info": info}
