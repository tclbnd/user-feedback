"""Semantic edge cases: channels, silence, partial tail blocks, int16 limits."""
import json
import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
import reference  # noqa: E402

OV = ROOT / "overview.py"
WORK = Path(os.environ.get("T_WORK", ROOT / "twork")) / "semantics"
LEAF = 256


def run(*args, check=True):
    proc = subprocess.run([sys.executable, str(OV), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("cmd failed: %s\n%s" % (args, proc.stderr))
    return proc


def make_source(path, frames, channels, pattern):
    with open(path, "wb") as fh:
        for i in range(frames):
            for ch in range(channels):
                v = pattern(i, ch)
                fh.write(struct.pack("<h", v))


def check(root, source, channels, segments, ranges):
    rpath = WORK / "r.json"
    rpath.write_text(json.dumps({"ranges": ranges}) + "\n")
    epath = WORK / "e.json"
    epath.write_text(json.dumps({"segments": segments}) + "\n")
    run(root, "edit", epath)
    got = json.loads(run(root, "query", rpath).stdout)
    want = reference.reference_query(source, channels, segments, ranges)
    for g, w in zip(got["ranges"], want):
        assert g["id"] == w["id"]
        for gc, wc in zip(g["channels"], w["channels"]):
            assert gc["min"] == wc["min"] and gc["max"] == wc["max"], (g, w)
            assert math.isclose(gc["rms"], wc["rms"], rel_tol=1e-12,
                                abs_tol=1e-9), (g, w)


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    # 1. every channel count 1..8 with extreme int16 values and partial tail
    for channels in range(1, 9):
        frames = LEAF + 37  # one full leaf + a short partial tail block
        source = WORK / ("src%d.pcm" % channels)

        def pattern(i, ch, channels=channels):
            table = (-32768, 32767, 0, -1, 1)
            return table[(i + ch * 7) % len(table)]

        make_source(source, frames, channels, pattern)
        root = WORK / ("idx%d" % channels)
        run(root, "build", source, "--channels", channels)
        ranges = [
            {"id": "full", "start": 0, "end": frames},
            {"id": "tail", "start": LEAF - 3, "end": frames},
            {"id": "partial", "start": LEAF, "end": frames},
            {"id": "inside", "start": 100, "end": 200},
            {"id": "empty", "start": 5, "end": 5},
        ]
        check(root, source, channels,
              [{"start": 0, "end": frames}], ranges)
    print("PASS channels 1..8 with extreme values and partial tail block")

    # 2. silence semantics: min/max 0, rms 0, but frames still counted
    channels = 2
    source = WORK / "srcS.pcm"
    make_source(source, 1000, channels, lambda i, ch: 100 + i)
    root = WORK / "idxS"
    run(root, "build", source, "--channels", channels)
    segments = [{"silence": 300}, {"start": 0, "end": 300},
                {"silence": 1}, {"start": 0, "end": 0}]
    ranges = [{"id": "z", "start": 0, "end": 300},
              {"id": "mixed", "start": 299, "end": 302},
              {"id": "zero-len-src", "start": 600, "end": 601},
              {"id": "all", "start": 0, "end": 601}]
    check(root, source, channels, segments, ranges)
    print("PASS silence counts frames and yields zero peaks/RMS")

    # 3. empty timeline (all segments zero-length)
    epath = WORK / "empty.json"
    epath.write_text(json.dumps({"segments": [
        {"start": 0, "end": 0}, {"silence": 0}]}) + "\n")
    run(root, "edit", epath)
    rpath = WORK / "er.json"
    rpath.write_text(json.dumps({"ranges": [
        {"id": "only-empty", "start": 0, "end": 0}]}) + "\n")
    out = json.loads(run(root, "query", rpath).stdout)
    for row in out["ranges"][0]["channels"]:
        assert row == {"min": 0, "max": 0, "rms": 0.0}
    print("PASS empty timeline answers zeroed ranges without raw I/O")

    # 4. frame misalignment is rejected at build
    bad = WORK / "bad.pcm"
    bad.write_bytes(b"\x01\x02\x03")  # 3 bytes, 2ch*2 = 4 byte frames
    badroot = WORK / "idxbad"
    proc = run(badroot, "build", bad, "--channels", 2, check=False)
    assert proc.returncode == 2 and "frame-aligned" in proc.stderr
    print("PASS unaligned PCM rejected")
    print("ALL SEMANTIC TESTS PASSED")


if __name__ == "__main__":
    main()
