"""Summary of one SoundFont 2 bank."""
from .hydra import parse
from .riff import Sf2Error, load


def summary(data):
    found = load(data)
    for need in (b"phdr", b"pbag", b"pgen", b"inst", b"ibag", b"igen", b"shdr"):
        if need not in found:
            raise Sf2Error("missing %s chunk" % need.decode())
    presets, instruments, samples = parse(found)
    ifil = found.get(b"ifil", b"\0\0\0\0")
    return {
        "name": found.get(b"INAM", b"").split(b"\0")[0].decode("latin-1"),
        "version": str(int.from_bytes(ifil[0:2], "little")),
        "sample_data_frames": len(found.get(b"smpl", b"")) // 2,
        "has_24bit": b"sm24" in found,
        "presets": presets, "instruments": instruments, "samples": samples,
    }
