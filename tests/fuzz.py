"""Randomised truncation fuzzing.

For hundreds of cut points on both reference fixtures, assert that the
repairer either succeeds with a structurally valid container whose packets
are all genuine reference packets, or refuses with a locatable RepairError.
No cut point may crash or emit a foreign packet.
"""

import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)
sys.path.insert(0, HERE)

from mkvrecover.repair import RepairError, repair
import verify


def demux_bytes(data):
    handle = tempfile.NamedTemporaryFile(suffix=".mkv", delete=False)
    handle.write(data)
    handle.close()
    try:
        return verify.Demuxed(handle.name)
    finally:
        os.unlink(handle.name)


def key_of(block):
    return (block["track"], block["time"], block["lacing"],
            tuple(block["frame_sizes"]), block["sha256"])


def run(label, path, count, seed):
    data = open(path, "rb").read()
    ref = verify.Demuxed(path)
    ref_keys = {key_of(b) for b in ref.blocks}
    counts = {"ok": 0, "refused": 0, "crash": 0, "bad": 0}
    random.seed(seed)
    points = sorted(random.sample(range(20, len(data)), count))
    for cut in points:
        try:
            output, report = repair(data[:cut])
        except RepairError:
            counts["refused"] += 1
            continue
        except Exception as exc:                       # noqa: BLE001
            counts["crash"] += 1
            print("  CRASH %s cut=%d %s: %s"
                  % (label, cut, type(exc).__name__, exc))
            continue
        try:
            demuxed = demux_bytes(output)
            problems = demuxed.validate_indexes()
        except Exception as exc:                       # noqa: BLE001
            counts["bad"] += 1
            print("  BAD %s cut=%d %s: %s"
                  % (label, cut, type(exc).__name__, exc))
            continue
        if problems:
            counts["bad"] += 1
            print("  INDEX %s cut=%d %s" % (label, cut, problems[:2]))
            continue
        foreign = [b for b in demuxed.blocks if key_of(b) not in ref_keys]
        if foreign:
            counts["bad"] += 1
            print("  FOREIGN %s cut=%d track=%d time=%d"
                  % (label, cut, foreign[0]["track"], foreign[0]["time"]))
            continue
        recovered = len(report["recovered"])
        if recovered != len(demuxed.blocks):
            counts["bad"] += 1
            print("  COUNT %s cut=%d manifest=%d demuxed=%d"
                  % (label, cut, recovered, len(demuxed.blocks)))
            continue
        counts["ok"] += 1
    print("%s: %s" % (label, counts))
    return counts


def main():
    samples = os.path.join(BASE, "samples")
    failures = 0
    for label, name, count, seed in (
            ("fixed-size", "ref_full.mkv", 400, 1),
            ("unknown-size", "ref_unknown.mkv", 250, 2),
            ("demo.mkv", os.path.join(BASE, "demo.mkv"), 200, 3)):
        path = name if os.path.isabs(name) else os.path.join(samples, name)
        counts = run(label, path, count, seed)
        failures += counts["crash"] + counts["bad"]
    print("\nfuzz failures:", failures)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
