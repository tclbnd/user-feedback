#!/usr/bin/env python3
"""Parse .zaf framed audio container into a frame-status report."""
import json, struct, sys, zlib

MAGIC = b"ZQAF0001"

def parse(path):
    data = open(path, "rb").read()
    if not data.startswith(MAGIC):
        print("magic mismatch", file=sys.stderr)
        sys.exit(2)
    frames, gaps = [], []
    off = len(MAGIC)
    while off + 12 <= len(data):
        seq, length, crc = struct.unpack(">III", data[off:off + 12])
        payload = data[off + 12:off + 12 + length]
        if zlib.crc32(data[off:off + 12 + length]) != crc:
            print(f"corrupt frame at {off}", file=sys.stderr)
            sys.exit(2)  # TODO: should skip bad frame and resync
        frames.append({"seq": seq, "offset": off, "length": length, "status": "ok"})
        off += 12 + length
    if off != len(data):
        gaps.append({"offset": off, "reason": "truncated"})
    ok = [f for f in frames if f["status"] == "ok"]
    regions = []
    if ok:
        regions.append({"start": ok[0]["offset"], "end": ok[-1]["offset"] + 12 + ok[-1]["length"],
                        "frames": len(ok)})
    return {"frames": frames, "gaps": gaps, "regions": regions,
            "summary": {"frames_total": len(frames), "frames_ok": len(ok),
                        "frames_bad": len(frames) - len(ok), "gaps": len(gaps),
                        "bytes_recovered": sum(f["length"] for f in ok)}}

def main():
    result = parse(sys.argv[1])
    with open(sys.argv[2], "w") as fh:
        json.dump(result, fh)

if __name__ == "__main__":
    main()
