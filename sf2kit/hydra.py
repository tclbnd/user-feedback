"""The pdta 'hydra': presets, instruments, zones, generators and sample headers.

Every hydra list ends with a terminal record (EOP, EOI or EOS) that only marks
the end of the preceding record's zone list; it is not a preset, instrument or
sample and is never reported.
"""
import struct

from .riff import Sf2Error

KEY_RANGE, INSTRUMENT, SAMPLE_ID = 43, 41, 53
TYPES = {1: "mono", 2: "right", 4: "left", 8: "linked"}


def records(blob, size, fmt):
    if len(blob) % size:
        raise Sf2Error("chunk size is not a multiple of %d" % size)
    return [struct.unpack_from(fmt, blob, i) for i in range(0, len(blob), size)]


def _name(b):
    return b.split(b"\0")[0].decode("latin-1").rstrip()


def _without_terminal(rows, terminal):
    """Drop the terminal record of a hydra list.

    Every preset, instrument and sample list ends with one record (EOP, EOI or
    EOS); it only marks the end of the previous record's zone list and is not a
    preset, instrument or sample itself.
    """
    if rows and rows[-1][0].split(b"\0")[0] == terminal:
        return rows[:-1]
    return rows


def _zones(headers, bag_index, bags, gens):
    """Generator lists of each header's zones, following wBagNdx and wGenNdx."""
    starts = [b[0] for b in bags] + [len(gens)]
    out = []
    for i, h in enumerate(headers):
        end = headers[i + 1][bag_index] if i + 1 < len(headers) else len(bags) - 1
        out.append([gens[starts[z]:starts[z + 1]] for z in range(h[bag_index], end)])
    return out


def _split_global(zones, reference):
    """A leading zone without the reference generator is the global zone."""
    if zones and not any(oper == reference for oper, _ in zones[0]):
        return True, zones[1:]
    return False, zones


def _key_range(zone):
    """gen 43 carries two unsigned bytes, the low and high key of the range."""
    for oper, amount in zone:
        if oper == KEY_RANGE:
            return "%d-%d" % (amount & 0xFF, amount >> 8)
    return "0-127"


def _loop_point(pos, start):
    """A loop point is absolute in the smpl chunk; zero means "no loop"."""
    return 0 if pos == 0 else max(0, pos - start)


def parse(found):
    phdr = _without_terminal(records(found[b"phdr"], 38, "<20sHHHIII"), b"EOP")
    pbag = records(found[b"pbag"], 4, "<HH")
    pgen = records(found[b"pgen"], 4, "<HH")
    inst = _without_terminal(records(found[b"inst"], 22, "<20sH"), b"EOI")
    ibag = records(found[b"ibag"], 4, "<HH")
    igen = records(found[b"igen"], 4, "<HH")
    shdr = _without_terminal(records(found[b"shdr"], 46, "<20sIIIIIBbHH"), b"EOS")
    samples = []
    # dwEnd points one frame past the sample, so the frame count is
    # dwEnd - dwStart; the loop points are made relative to the sample start.
    # chPitchCorrection is a signed byte.
    for name, start, end, ls, le, rate, pitch, corr, link, kind in shdr:
        samples.append({"name": _name(name), "frames": end - start, "sample_rate": rate,
                        "loop_start": _loop_point(ls, start), "loop_end": _loop_point(le, start),
                        "root_key": pitch, "correction": corr, "type": TYPES.get(kind, "unknown")})

    instruments = []
    for (name, _), zones in zip(inst, _zones(inst, 1, ibag, igen)):
        global_zone, zones = _split_global(zones, SAMPLE_ID)
        names, ranges = [], []
        for z in zones:
            d = dict(z)
            if SAMPLE_ID in d:
                names.append(samples[d[SAMPLE_ID]]["name"])
            ranges.append(_key_range(z))
        instruments.append({"name": _name(name), "zones": len(zones), "global_zone": global_zone,
                            "samples": names, "key_ranges": ranges})
    presets = []
    for (name, preset, bank, *_), zones in zip(phdr, _zones(phdr, 3, pbag, pgen)):
        global_zone, zones = _split_global(zones, INSTRUMENT)
        used = [instruments[dict(z)[INSTRUMENT]]["name"] for z in zones if INSTRUMENT in dict(z)]
        presets.append({"name": _name(name), "bank": bank, "preset": preset, "zones": len(zones),
                        "global_zone": global_zone, "instruments": used})
    return presets, instruments, samples
