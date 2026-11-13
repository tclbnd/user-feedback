"""Acceptance 2: bounded query I/O after indexing + fault-injection safety.

Two sources differ 10x in sample count.  After build, 64 equal-width overview
ranges are queried.  The indexed query must not scale linearly with source
length: index_pages grows at most logarithmically and raw_bytes stay bounded
by the two partial leaf edges per range.

Fault injections assert:
  * a corrupted index page is detected and fully rebuilt from the source;
  * a source whose recorded identity no longer matches is rejected, never
    answered with stale index peaks;
  * --crash-before-publish leaves only the previous complete version visible;
  * no half-written *.tmp file is ever exposed as a readable version.
"""
import json
import os
import random
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OV = ROOT / "overview.py"
WORK = Path(os.environ.get("T_WORK", ROOT / "twork")) / "scaling"
BUCKETS = 64
LEAF = 256


def run(*args, check=True):
    proc = subprocess.run([sys.executable, str(OV), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("cmd failed: %s\n%s" % (args, proc.stderr))
    return proc


def write_pcm(path, frames, channels, seed):
    rng = random.Random(seed)
    with open(path, "wb") as fh:
        for _ in range(frames):
            frame = bytearray()
            for ch in range(channels):
                frame += struct.pack("<h", rng.randint(-30000, 30000))
            fh.write(frame)


def equal_width_ranges(frames):
    step = frames / BUCKETS
    ranges = []
    for i in range(BUCKETS):
        start = int(round(i * step))
        end = int(round((i + 1) * step))
        ranges.append({"id": i, "start": start, "end": end})
    return ranges


def measure(frames, channels=2):
    name = "f%d" % frames
    wd = WORK / name
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    source = wd / "src.pcm"
    write_pcm(source, frames, channels, seed=frames)
    idx = wd / "idx"
    run(idx, "build", source, "--channels", channels)
    rpath = wd / "ranges.json"
    rpath.write_text(json.dumps({"ranges": equal_width_ranges(frames)}) + "\n")
    # cold query: reopen every file, so page counts are not cache-inflated
    proc = run(idx, "query", rpath)
    out = json.loads(proc.stdout)
    size = source.stat().st_size
    return {"frames": frames, "source_bytes": size,
            "raw_bytes": out["raw_bytes"], "index_pages": out["index_pages"],
            "buckets": len(out["ranges"])}


def test_scaling():
    small = measure(200_000)
    big = measure(2_000_000)
    print(json.dumps({"small": small, "big": big}, indent=2, sort_keys=True))
    # raw bytes must stay bounded by edges: at most 2 partial leaves/range
    edge_cap = BUCKETS * 2 * LEAF * 2 * 2
    assert small["raw_bytes"] <= edge_cap, small
    assert big["raw_bytes"] <= edge_cap, big
    # 10x source length must not produce 10x query traffic: the indexed path
    # is sublinear (upper bound is a constant per bucket plus log levels)
    ratio = big["source_bytes"] / small["source_bytes"]
    assert ratio >= 9.9
    assert big["index_pages"] < small["index_pages"] * ratio / 2, (small, big)
    assert big["index_pages"] <= BUCKETS * 2 + 8, (small, big)
    # the unindexed baseline would read the whole file each time
    assert big["raw_bytes"] < big["source_bytes"] / 30
    print("PASS scaling: %.1fx data, index_pages %d->%d, raw %d->%d"
          % (ratio, small["index_pages"], big["index_pages"],
             small["raw_bytes"], big["raw_bytes"]))
    return small, big


def corrupt_page(path, page):
    """Flip bytes inside the first block record of a data page."""
    with open(path, "r+b") as fh:
        fh.seek(page * 4096)
        original = bytearray(fh.read(8))
        flipped = bytes(b ^ 0x5A for b in original)
        if flipped == bytes(original):  # pragma: no cover - impossible for xor
            flipped = bytes((b + 1) & 0xFF for b in original)
        fh.seek(page * 4096)
        fh.write(flipped)


def test_corruption_rebuild():
    wd = WORK / "fault"
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    source = wd / "src.pcm"
    write_pcm(source, 5000, 2, seed=7)
    idx = wd / "idx"
    run(idx, "build", source, "--channels", 2)
    rpath = wd / "ranges.json"
    # ranges deliberately cut leaf blocks (unaligned edges), forcing leaf
    # data pages to be read and CRC-checked rather than served by parent blocks
    rpath.write_text(json.dumps({"ranges": [
        {"id": "x", "start": 0, "end": 5000},
        {"id": "edge", "start": 13, "end": 4900},
        {"id": "leaf", "start": 300, "end": 700},
    ]}) + "\n")
    good = json.loads(run(idx, "query", rpath).stdout)

    # corrupt a data page of the derived index -> CRC failure then rebuild
    target = idx / "edl" / "index-1.idx"
    corrupt_page(target, page=1)
    recovered = json.loads(run(idx, "query", rpath).stdout)
    assert recovered["ranges"] == good["ranges"]
    log = [json.loads(line) for line in (idx / "stats.log").read_text().splitlines()]
    assert any(e.get("op") == "recover-derived-index" for e in log), log
    # the rebuilt file is readable again without recovery
    again = json.loads(run(idx, "query", rpath).stdout)
    assert again["ranges"] == good["ranges"]

    # corrupt the source index root-block page -> detected and rebuilt
    src_target = idx / "source.idx"
    import overview as ov  # reuse the real layout to find the top-level page
    ov_reader = ov.IndexReader(src_target, ov.KIND_SOURCE)
    top = ov_reader.nlevels - 1
    start_page = ov_reader._level_regions[top][0]
    ov_reader.close()
    corrupt_page(src_target, page=start_page)
    rec2 = json.loads(run(idx, "query", rpath).stdout)
    assert rec2["ranges"] == good["ranges"]
    log = [json.loads(line) for line in (idx / "stats.log").read_text().splitlines()]
    assert any(e.get("op") == "recover-source-index" for e in log)
    print("PASS corruption: damaged pages rejected and rebuilt from source")

    # delete the whole derived index file -> missing-file recovery path
    (idx / "edl" / "index-1.idx").unlink()
    rec3 = json.loads(run(idx, "query", rpath).stdout)
    assert rec3["ranges"] == good["ranges"]
    assert (idx / "edl" / "index-1.idx").exists()
    print("PASS missing index file is rebuilt without stale data")


def test_identity_mismatch():
    wd = WORK / "fault"
    idx = wd / "idx"
    rpath = wd / "ranges.json"
    # replace source contents with different data at the same path
    source = wd / "src.pcm"
    write_pcm(source, 5000, 2, seed=999)
    proc = run(idx, "query", rpath, check=False)
    assert proc.returncode == 2, proc.stdout
    assert "IdentityMismatch" in proc.stderr, proc.stderr
    # mtime-only change is also rejected rather than silently trusted
    os.utime(source, None)
    proc = run(idx, "query", rpath, check=False)
    assert proc.returncode == 2 and "IdentityMismatch" in proc.stderr
    print("PASS identity: mismatched source is rejected, no stale peaks served")


def test_crash_and_half_update():
    wd = WORK / "fault2"
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    source = wd / "src.pcm"
    write_pcm(source, 3000, 1, seed=11)
    idx = wd / "idx"
    run(idx, "build", source, "--channels", 1)
    rpath = wd / "ranges.json"
    rpath.write_text(json.dumps(
        {"ranges": [{"id": "x", "start": 0, "end": 3000}]}) + "\n")
    before = json.loads(run(idx, "query", rpath).stdout)

    edl = wd / "edl.json"
    edl.write_text(json.dumps(
        {"segments": [{"silence": 3000}]}) + "\n")
    proc = run(idx, "edit", edl, "--crash-before-publish", check=False)
    assert proc.returncode == 97, proc.stderr
    # no half-published artifacts and current version still 1
    leftovers = list((idx / "edl").glob("*.tmp"))
    assert not leftovers, leftovers
    assert json.loads((idx / "current.txt").read_text())["version"] == 1
    assert not (idx / "edl" / "index-2.idx").exists()
    assert not (idx / "edl" / "edl-2.json").exists()
    after = json.loads(run(idx, "query", rpath).stdout)
    assert after == before
    # a normal edit afterwards still advances to version 2 cleanly
    proc = run(idx, "edit", edl)
    assert json.loads(proc.stdout)["version"] == 2
    assert json.loads((idx / "current.txt").read_text())["version"] == 2
    v2 = json.loads(run(idx, "query", rpath).stdout)
    assert v2["version"] == 2
    assert v2["ranges"][0]["channels"][0] == {"min": 0, "max": 0, "rms": 0.0}
    # old version remains independently readable
    v1 = json.loads(run(idx, "query", rpath, "--version", 1).stdout)
    assert v1 == before
    print("PASS publish: crash exposes no half version; readers see complete sets")


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    test_scaling()
    test_corruption_rebuild()
    test_identity_mismatch()
    test_crash_and_half_update()
    print("ALL SCALING/FAULT TESTS PASSED")


if __name__ == "__main__":
    main()
