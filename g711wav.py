"""G.711 call recordings in WAV files.

    python3 g711wav.py info call.wav ...           format, channels, rate, frames, duration
    python3 g711wav.py decode call.wav out.wav     G.711 WAV -> 16-bit PCM WAV
    python3 g711wav.py encode alaw in.wav out.wav  16-bit PCM WAV -> A-law (or ulaw) WAV
"""
import json
import sys

from telcodec import wav


def main(args):
    try:
        if args[0] == "info":
            print(json.dumps([{"file": p, **wav.info(open(p, "rb").read())} for p in args[1:]], indent=1))
        elif args[0] == "decode":
            open(args[2], "wb").write(wav.decode(open(args[1], "rb").read()))
        elif args[0] == "encode":
            open(args[3], "wb").write(wav.encode(open(args[2], "rb").read(), args[1]))
    except wav.WavError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
