"""Write the AMR payloads of one call leg as a storage-format file.

    python3 rtp2amr.py leg.json out.amr

leg.json: {"codec": "amr-nb" | "amr-wb",
           "mode": "octet-aligned" | "bandwidth-efficient",
           "payloads": ["<hex>", ...]}
"""
import json
import sys

from amrfile.frames import AmrError
from amrfile.rtp import depacketize


def main(src, dst):
    leg = json.load(open(src))
    try:
        data = depacketize([bytes.fromhex(p) for p in leg["payloads"]], leg.get("codec", "amr-nb"),
                           leg.get("mode", "octet-aligned"))
    except (AmrError, IndexError) as exc:
        print(json.dumps({"error": str(exc) or type(exc).__name__}))
        sys.exit(1)
    open(dst, "wb").write(data)
    print(json.dumps({"bytes": len(data)}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
