"""Evaluate G.711 calls from a JSON list, or convert raw files.

    python3 g711calc.py calls.json out.json
    python3 g711calc.py --decode ulaw call.ul out.pcm     8-bit codes -> 16-bit little-endian PCM
    python3 g711calc.py --encode alaw in.pcm out.al        16-bit little-endian PCM -> 8-bit codes
"""
import json
import struct
import sys

from telcodec import g711

FUNCS = {"ulaw_encode": g711.ulaw_encode, "ulaw_decode": g711.ulaw_decode,
         "alaw_encode": g711.alaw_encode, "alaw_decode": g711.alaw_decode}


def main(args):
    if args[0] in ("--decode", "--encode"):
        law, src, dst = args[1], args[2], args[3]
        data = open(src, "rb").read()
        if args[0] == "--decode":
            fn = FUNCS[f"{law}_decode"]
            out = b"".join(struct.pack("<h", fn(b)) for b in data)
        else:
            fn = FUNCS[f"{law}_encode"]
            out = bytes(fn(s) for (s,) in struct.iter_unpack("<h", data[:len(data) // 2 * 2]))
        open(dst, "wb").write(out)
        return
    calls = json.load(open(args[0]))
    results = [{"call": c, "value": FUNCS[c[0]](*c[1:])} for c in calls]
    json.dump(results, open(args[1], "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
