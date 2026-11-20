"""Print the SPS summary of H.264 Annex B files as JSON.

    python3 h264info.py clip.h264 ...
"""
import json
import sys

from spsinfo.annexb import nal_units
from spsinfo.sps import SpsError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            data = open(path, "rb").read()
            sps = next((n for n in nal_units(data) if n and n[0] & 0x1F == 7), None)
            if sps is None:
                raise SpsError("no SPS in stream")
            out.append({"file": path, **parse(sps)})
        except (SpsError, OSError, IndexError) as exc:
            out.append({"file": path, "error": str(exc) or type(exc).__name__})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
