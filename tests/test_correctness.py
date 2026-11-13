"""Acceptance 1: sample-exact equivalence over 1000 seeded ranges x 40 edits.

The oracle is tests/reference.py, which is a fully independent implementation.
Every generated range is compared per channel on min, max and RMS.  Ranges are
forced to land on index-block boundaries (leaf=1024 frames, fanout=32) and on
partial-tail offsets so coverage spans every index level and the short final
block of each level.
"""
import json
import math
import os
import random
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reference  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OV = ROOT / "overview.py"
WORK = Path(os.environ.get("T_WORK", ROOT / "twork"))
LEAF = 256
FANOUT = 32
SEED_BASE = 20261113
N_RANGES = 1000
N_EDITS = 40
RMS_REL = 1e-12
RMS_ABS = 1e-9


def write_pcm(path, frames, channels, seed):
    rng = random.Random(seed)
    with open(path, "wb") as fh:
        chunk = bytearray()
        for _ in range(frames):
            for ch in range(channels):
                # include extreme int16 values and long constant runs
                pick = rng.random()
                if pick < 0.01:
                    v = rng.choice((-32768, 32767, 0))
                elif pick < 0.05:
                    v = rng.choice((-1, 0, 1))
                else:
                    v = rng.randint(-20000, 20000)
                chunk += struct.pack("<h", v)
            if len(chunk) >= 1 << 16:
                fh.write(chunk)
                chunk.clear()
        fh.write(chunk)


def run(*args, check=True):
    proc = subprocess.run([sys.executable, str(OV), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("cmd failed: %s\n%s" % (args, proc.stderr))
    return proc


def rms_close(a, b):
    return math.isclose(a, b, rel_tol=RMS_REL, abs_tol=RMS_ABS)


def boundary_offsets(length):
    """Offsets guaranteed to exercise every level and partial tail blocks."""
    offsets = {0, length}
    span = LEAF
    level = 0
    while span <= length:
        for mult in (0, 1, 2):
            for delta in (-1, 0, 1):
                v = mult * span + delta
                if 0 <= v <= length:
                    offsets.add(v)
        # final partial block boundary
        last_full = (length // span) * span
        for v in (last_full, last_full - 1, min(length, last_full + 1),
                  length - 1):
            if 0 <= v <= length:
                offsets.add(v)
        span *= FANOUT
        level += 1
    for delta in (0, 1, 2, 3, 7, 31, 33, 1023, 1025):
        if delta <= length:
            offsets.add(delta)
    return sorted(offsets)


def make_ranges(timeline_frames, seed, count):
    rng = random.Random(seed)
    boundaries = boundary_offsets(timeline_frames)
    ranges = []
    # half deterministic boundary pairs, half fully random
    for i in range(count // 2):
        start = rng.choice(boundaries)
        end = rng.choice([v for v in boundaries if v >= start])
        ranges.append({"id": "b%d" % i, "start": start, "end": end})
    while len(ranges) < count:
        i = len(ranges)
        start = rng.randint(0, timeline_frames)
        end = rng.randint(start, timeline_frames)
        ranges.append({"id": "r%d" % i, "start": start, "end": end})
    # explicit empty and full-width ranges on every round
    ranges.append({"id": "empty", "start": 0, "end": 0})
    ranges.append({"id": "full", "start": 0, "end": timeline_frames})
    return ranges


def random_edl(rng, source_frames):
    segs = []
    target = rng.randint(0, 6)
    for _ in range(target):
        kind = rng.random()
        if kind < 0.25:
            segs.append({"silence": rng.randint(0, 2 * LEAF + 5)})
        else:
            start = rng.randint(0, source_frames)
            end = rng.randint(start, source_frames)
            segs.append({"start": start, "end": end})
            if rng.random() < 0.3:  # repeated / reordered source intervals
                segs.append({"start": start, "end": end})
    if not segs:
        segs = [{"start": 0, "end": source_frames}]
    return {"segments": segs}


def compare(got, want, context):
    got_by_id = {r["id"]: r for r in got["ranges"]}
    assert len(got_by_id) == len(want["ranges"]), context
    for w in want["ranges"]:
        g = got_by_id[w["id"]]
        assert len(g["channels"]) == len(w["channels"]), context
        for ch, (gc, wc) in enumerate(zip(g["channels"], w["channels"])):
            if gc["min"] != wc["min"] or gc["max"] != wc["max"]:
                raise AssertionError(
                    "%s range=%s ch=%d peak %s != %s"
                    % (context, w["id"], ch, gc, wc))
            if not rms_close(gc["rms"], wc["rms"]):
                raise AssertionError(
                    "%s range=%s ch=%d rms %.15g != %.15g"
                    % (context, w["id"], ch, gc["rms"], wc["rms"]))


def main():
    WORK.mkdir(exist_ok=True)
    # 35,000 frames -> leaf level 35 blocks, level1 (32k span) 2 blocks,
    # level2 (1,048,576 span) 1 block: three levels with partial blocks each.
    frames = 35 * LEAF + 777
    channels = 3
    source = WORK / "src.pcm"
    write_pcm(source, frames, channels, SEED_BASE)
    root = WORK / "idx"
    if root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            else:
                p.rmdir()
    run(root, "build", source, "--channels", channels)
    meta = json.loads((root / "meta.json").read_text())

    total_checks = 0
    timeline = [{"start": 0, "end": frames}]
    for edit_round in range(N_EDITS + 1):
        version = edit_round + 1
        if edit_round:
            rng = random.Random(SEED_BASE + edit_round)
            edl = random_edl(rng, frames)
            edl_path = WORK / ("edl-%d.json" % version)
            edl_path.write_text(json.dumps(edl) + "\n")
            run(root, "edit", edl_path)
            timeline = edl["segments"]
        tl_frames = sum(
            s["silence"] if "silence" in s else s["end"] - s["start"]
            for s in timeline)
        seed = SEED_BASE * 1000 + version
        ranges = make_ranges(tl_frames, seed, N_RANGES)
        rpath = WORK / ("ranges-%d.json" % version)
        rpath.write_text(json.dumps({"ranges": ranges}) + "\n")
        proc = run(root, "query", rpath)
        got = json.loads(proc.stdout)
        assert got["version"] == version
        want_rows = reference.reference_query(
            source, channels, timeline, ranges)
        compare(got, {"ranges": want_rows},
                "edit-round=%d version=%d" % (edit_round, version))
        total_checks += len(ranges) * channels * 3
    print("PASS correctness: %d edits, %d ranges/round, %d scalar checks"
          % (N_EDITS, N_RANGES + 2, total_checks))


if __name__ == "__main__":
    main()
