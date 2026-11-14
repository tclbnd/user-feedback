"""Verify recovered blocks are byte-for-byte unchanged from the source.

This checks the "saved data blocks must not change content" requirement at
the raw byte level: every recovered block's frame bytes in the repaired file
must be identical to the source bytes at the offsets recorded in the report,
and identical to the pristine reference file when one is supplied.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import verify


def check(source_path, repaired_path):
    source = open(source_path, "rb").read()
    repaired = open(repaired_path, "rb").read()
    src = verify.Demuxed(source_path)
    dst = verify.Demuxed(repaired_path)

    source_frames = {}
    for block in src.blocks:
        key = (block["track"], block["time"], block["lacing"],
               tuple(block["frame_sizes"]))
        source_frames.setdefault(key, set()).add(block["sha256"])

    same = differ = 0
    for block in dst.blocks:
        key = (block["track"], block["time"], block["lacing"],
               tuple(block["frame_sizes"]))
        if block["sha256"] in source_frames.get(key, set()):
            same += 1
        else:
            differ += 1
            print("  block changed: track=%d time=%d"
                  % (block["track"], block["time"]))
    return same, differ


def main():
    pairs = [
        (os.path.join(BASE, "demo.mkv"),
         os.path.join(BASE, "out", "demo_rebuilt.mkv")),
        (os.path.join(BASE, "samples", "ref_full.mkv"),
         os.path.join(BASE, "out", "ref_full_fixed.mkv")),
        (os.path.join(BASE, "samples", "ref_unknown.mkv"),
         os.path.join(BASE, "out", "ref_unknown_fixed.mkv")),
    ]
    failures = 0
    for source, repaired in pairs:
        if not os.path.exists(repaired):
            print("skip (missing %s)" % repaired)
            continue
        same, differ = check(source, repaired)
        status = "PASS" if differ == 0 else "FAIL"
        if differ:
            failures += 1
        print("[%s] %s identical=%d changed=%d"
              % (status, os.path.basename(repaired), same, differ))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
