"""The pdta 'hydra': presets, instruments, zones, generators and sample headers."""
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


def _zones(headers, bag_index, bags, gens):
    out = []
    for i, h in enumerate(headers):
        end = headers[i + 1][bag_index] if i + 1 < len(headers) else len(bags) - 1
        zones = []
        for z in range(h[bag_index], end):
            zones.append(gens[bags[z][0]:bags[z + 1][0]])
        out.append(zones)
    return out


def parse(found):
    phdr = records(found[b"phdr"], 38, "<20sHHHIII")
    pbag = records(found[b"pbag"], 4, "<HH")
    pgen = records(found[b"pgen"], 4, "<HH")
    inst = records(found[b"inst"], 22, "<20sH")
    ibag = records(found[b"ibag"], 4, "<HH")
    igen = records(found[b"igen"], 4, "<HH")
    shdr = records(found[b"shdr"], 46, "<20sIIIIIBBHH")
    samples = []
    for name, start, end, ls, le, rate, pitch, corr, link, kind in shdr:
        samples.append({"name": _name(name), "frames": end - start + 1, "sample_rate": rate,
                        "loop_start": ls, "loop_end": le, "root_key": pitch,
                        "correction": corr, "type": TYPES.get(kind, "unknown")})
    instruments = []
    for (name, _), zones in zip(inst, _zones(inst, 1, ibag, igen)):
        names, ranges = [], []
        for z in zones:
            d = dict(z)
            if SAMPLE_ID in d:
                names.append(samples[d[SAMPLE_ID]]["name"])
            ranges.append(str(d.get(KEY_RANGE, 127)))
        instruments.append({"name": _name(name), "zones": len(zones), "global_zone": False,
                            "samples": names, "key_ranges": ranges})
    presets = []
    for (name, preset, bank, *_), zones in zip(phdr, _zones(phdr, 3, pbag, pgen)):
        used = [instruments[dict(z)[INSTRUMENT]]["name"] for z in zones if INSTRUMENT in dict(z)]
        presets.append({"name": _name(name), "bank": bank, "preset": preset, "zones": len(zones),
                        "global_zone": False, "instruments": used})
    return presets, instruments, samples
