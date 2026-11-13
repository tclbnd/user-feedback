"""Reuse invariants for non-destructive EDL edits.

Asserts that:
  * a prefix-preserving edit reuses every unchanged leaf and parent block;
  * reuse is content-based: identical source intervals placed elsewhere still
    hit the fingerprint cache, while changed content forces a rebuild;
  * silence-only regions never read source bytes;
  * readers pin one EDL/index version pair, never mixing generations.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OV = ROOT / "overview.py"
WORK = Path(os.environ.get("T_WORK", ROOT / "twork")) / "reuse"
LEAF = 256


def run(*args, check=True):
    proc = subprocess.run([sys.executable, str(OV), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("cmd failed: %s\n%s" % (args, proc.stderr))
    return proc


def stats_log(idx):
    return [json.loads(line) for line in
            (idx / "stats.log").read_text().splitlines()]


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    source = WORK / "src.pcm"
    # deterministic ramp PCM, 2 channels, 4000 frames (16 leaf blocks)
    import struct
    with open(source, "wb") as fh:
        for i in range(4000):
            fh.write(struct.pack("<hh", i % 30000, -(i % 29000)))
    idx = WORK / "idx"
    run(idx, "build", source, "--channels", 2)

    def edit(segments):
        p = WORK / "edl.json"
        p.write_text(json.dumps({"segments": segments}) + "\n")
        return json.loads(run(idx, "edit", p).stdout)

    # edit 1: keep the first 3000 frames, append silence -> prefix untouched
    edit([{"start": 0, "end": 3000}, {"silence": 500}])
    log = [e for e in stats_log(idx) if e["op"] == "edit"][-1]
    # leaves fully inside the source prefix: floor(3000/256)=11; the leaf that
    # straddles the source/silence boundary must be rebuilt
    full_prefix_leaves = 3000 // LEAF
    assert log["leaves_reused"] == full_prefix_leaves, log
    assert log["leaves_rebuilt"] == log["leaves_total"] - full_prefix_leaves
    # the single parent covers a rebuilt boundary leaf, so it is not invariant
    assert log["parent_reused"] == 0 and log["parent_rebuilt"] >= 1, log
    print("PASS prefix reuse: reused %d/%d leaves, boundary leaf rebuilt"
          % (log["leaves_reused"], log["leaves_total"]))

    # edit 1b: identical frame count, fully reused prefix -> parents invariant
    edit([{"start": 0, "end": 3000}, {"silence": 500}])
    logb = [e for e in stats_log(idx) if e["op"] == "edit"][-1]
    assert logb["leaves_reused"] == logb["leaves_total"], logb
    assert logb["parent_reused"] == logb["parent_blocks"], logb
    assert logb["derived_raw_bytes"] == 0, logb
    print("PASS identical timeline reuses all leaves and parents, zero raw I/O")

    # edit 2: same source interval moved later in the timeline -> cache hit by
    # content fingerprint even though its timeline position changed
    out2 = edit([{"silence": 256}, {"start": 1000, "end": 2000}])
    log2 = [e for e in stats_log(idx) if e["op"] == "edit"][-1]
    assert log2["leaves_reused"] >= 1, log2
    print("PASS content-based reuse across repositioned interval")

    # edit 3: all silence -> zero source bytes read during index build
    edit([{"silence": 4000}])
    log3 = [e for e in stats_log(idx) if e["op"] == "edit"][-1]
    assert log3["derived_raw_bytes"] == 0, log3
    print("PASS silence timeline built without source I/O")

    # version pinning: querying v1 while v4 is current returns v1 peaks
    rpath = WORK / "r.json"
    rpath.write_text(json.dumps(
        {"ranges": [{"id": "all", "start": 0, "end": 4000}]}) + "\n")
    v1 = json.loads(run(idx, "query", rpath, "--version", 1).stdout)
    cur = json.loads(run(idx, "query", rpath).stdout)
    assert v1["version"] == 1 and cur["version"] == 5
    assert v1["ranges"] != cur["ranges"]  # silence timeline differs
    assert cur["ranges"][0]["channels"][0]["rms"] == 0.0
    print("PASS readers pin a consistent EDL/index version pair")

    # invalid EDLs are rejected and do not advance the version
    bad = WORK / "bad.json"
    bad.write_text(json.dumps(
        {"segments": [{"start": 10, "end": 5}]}) + "\n")
    proc = run(idx, "edit", bad, check=False)
    assert proc.returncode == 2
    assert json.loads((idx / "current.txt").read_text())["version"] == 5
    bad.write_text(json.dumps(
        {"segments": [{"start": 0, "end": 99999}]}) + "\n")
    assert run(idx, "edit", bad, check=False).returncode == 2
    print("PASS invalid EDL rejected without version advance")
    print("ALL REUSE TESTS PASSED")


if __name__ == "__main__":
    main()
