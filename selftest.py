#!/usr/bin/env python3
"""Self-test suite for the recovering .zaf parser.

Runs entirely against locally synthesised streams; every temporary file
is created under $TMPDIR.  Exits 0 only when all checks pass.

Coverage
--------
1. Stream with 3 known corruptions (2 bad-payload, 1 bad-header):
   frame table / gaps / regions / seq_gaps / summary vs. generator truth;
   recovered audio byte-identical to the original ok-frame samples.
2. In-span bad-header (length contradicts following content) and an
   intact-extent header field flip.
3. Mid-header and mid-payload truncation: byte-level gap location.
4. Random seq reordering: region lists identical across permutations.
5. Determinism: repeated parses identical; arbitrary-start recovery
   reproduces the suffix of the full-stream parse.
6. CLI contract: missing file / bad magic -> exit 2 + "magic" on stderr;
   magic present but all frames bad -> exit 0, frames_ok=0; magic only.
7. The shipped make_demo.py / unframe.py CLI end to end.
"""
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
UNFRAME = os.path.join(HERE, "unframe.py")
MAKE_DEMO = os.path.join(HERE, "make_demo.py")
MAGIC = b"ZQAF0001"
HEADER_LEN = 12

PASS_COUNT = 0
FAIL_COUNT = 0


def check(cond, label):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"  PASS  {label}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {label}")


def make_frame(seq, payload):
    body = struct.pack(">II", seq, len(payload)) + payload
    return struct.pack(">III", seq, len(payload), zlib.crc32(body)) + payload


def parse_bytes(data):
    """In-process parse (magic required), mirroring the CLI entry point."""
    if not data.startswith(MAGIC):
        raise AssertionError("synthetic stream missing magic")
    sys.path.insert(0, HERE)
    import unframe
    return unframe.parse_stream(data[len(MAGIC):], base=len(MAGIC))


def run_cli(args, expect_code=0):
    proc = subprocess.run(
        [sys.executable, "-I", UNFRAME] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check(proc.returncode == expect_code,
          f"cli exit {proc.returncode} == {expect_code} "
          f"({' '.join(args[:2])})")
    return proc


def tmp_path(name):
    return os.path.join(TMP, name)


TMP = tempfile.mkdtemp(prefix="zaf-selftest-")


def payload(seed, size=256):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(size))


# ---------------------------------------------------------------- test 1
print("test 1: three known corruptions vs generator truth")
samples = {i: payload(100 + i) for i in range(8)}
raw_frames = {i: make_frame(i, samples[i]) for i in range(8)}

# Lay frames out and remember every header offset (generator truth).
layout = b""
truth_offsets = {}
for i in range(8):
    truth_offsets[i] = len(MAGIC) + len(layout)
    layout += raw_frames[i]

stream = bytearray(MAGIC + layout)

# Corruption 1: payload bit flip on frame 2 -> bad-payload.
c1 = truth_offsets[2] + HEADER_LEN + len(samples[2]) - 1
stream[c1] ^= 0xFF
# Corruption 2: length field of frame 4 overruns the stream -> bad-header.
c2 = truth_offsets[4] + 4  # first byte of the length field
struct.pack_into(">I", stream, c2, 0xFFFFFFFF)
# Corruption 3: payload bit flip on frame 6 -> bad-payload.
c3 = truth_offsets[6] + HEADER_LEN + 17
stream[c3] ^= 0x01

report = parse_bytes(bytes(stream))

expected_frames = []
for i in range(8):
    if i == 4:
        continue  # frame 4 is unrecoverable as a frame -> gap only
    status = "bad-payload" if i in (2, 6) else "ok"
    expected_frames.append({"seq": i, "offset": truth_offsets[i],
                            "length": len(samples[i]), "status": status})
check(report["frames"] == expected_frames, "frame table matches generator truth")
check(report["gaps"] == [{"offset": truth_offsets[4], "reason": "bad-header"}],
      "single bad-header gap at frame 4 header offset")
check(report["seq_gaps"] == [
    {"offset": truth_offsets[5], "prev_seq": 3, "expected": 4, "actual": 5}],
    "seq discontinuity 3 -> 5 recorded truthfully")

# Regions: ok runs [0,1], [3], [5], [7] -> 4 regions, bad-payload splits.
expected_regions = []
run_specs = [(0, 1), (3, 3), (5, 5), (7, 7)]
for first, last in run_specs:
    expected_regions.append({
        "start": truth_offsets[first],
        "end": truth_offsets[last] + HEADER_LEN + len(samples[last]),
        "frames": last - first + 1})
check(report["regions"] == expected_regions, "four regions split by damage")

ok_seqs = [0, 1, 3, 5, 7]
check(report["summary"] == {
    "frames_total": 7, "frames_ok": 5, "frames_bad": 2, "gaps": 1,
    "bytes_recovered": sum(len(samples[i]) for i in ok_seqs)},
    "summary counts correct")

# Byte-exact audio recovery: extracted ok payloads equal original samples.
sys.path.insert(0, HERE)
import unframe
recovered = unframe.extract_ok_payload(bytes(stream), report)
expected_audio = b"".join(samples[i] for i in ok_seqs)
check(recovered == expected_audio,
      "recovered payload byte-identical to original ok-frame audio")
check(len(recovered) == sum(len(samples[i]) for i in ok_seqs),
      "no fabricated bytes: recovered length is exactly the ok payload sum")

# The damaged / gap byte ranges must not appear in any region.
region_span = b"".join(bytes(stream[r["start"]:r["end"]])
                       for r in report["regions"])
for bad_off in (c1, c3):
    check(not any(r["start"] <= bad_off < r["end"] for r in report["regions"]),
          f"damaged byte {bad_off} excluded from every region")
check(not any(r["start"] <= c2 < r["end"] for r in report["regions"]),
      "bad-header span excluded from every region")

# ---------------------------------------------------------------- test 2
print("test 2: bad-header (illegal length) and header field damage")
pa, pb, pc = payload(1, 64), payload(2, 64), payload(3, 64)
fa, fb, fc = make_frame(0, pa), make_frame(1, pb), make_frame(2, pc)
off_a, off_b, off_c = 8, 8 + len(fa), 8 + len(fa) + len(fb)

# 2a: length field overruns the stream, but a later intact frame still
# exists -> bad-header gap plus byte-level resync at frame B.
s2 = bytearray(MAGIC + fa + fb + fc)
struct.pack_into(">I", s2, off_a + 4, 0xFFFFFFFF)
r2 = parse_bytes(bytes(s2))
check(r2["frames"] == [
    {"seq": 1, "offset": off_b, "length": 64, "status": "ok"},
    {"seq": 2, "offset": off_c, "length": 64, "status": "ok"}],
    "overrun bad-header: frames B,C recovered after resync")
check(r2["gaps"] == [{"offset": off_a, "reason": "bad-header"}],
      "overrun bad-header gap located at A header byte offset")
check(r2["regions"] == [{"start": off_b,
                         "end": off_c + HEADER_LEN + 64, "frames": 2}],
      "resync yields one region B..C, no bytes fabricated in the gap")

# 2b: length stays inside the stream but contradicts following content.
# The extent is still trustworthy per contract -> bad-payload row with
# the exact frame range, skipped at frame granularity.  The fake span
# ends inside B, so B's own header is reached afterward and B,C survive.
s2b = bytearray(MAGIC + fa + fb + fc)
fake_len = len(fb) - 4  # claimed end lands 4 bytes before B's end
struct.pack_into(">I", s2b, off_a + 4, fake_len)
r2b = parse_bytes(bytes(s2b))
check(r2b["frames"][0] == {"seq": 0, "offset": off_a,
                           "length": fake_len, "status": "bad-payload"},
      "in-span length contradiction -> bad-payload with exact byte range")
check(r2b["gaps"] == [], "frame-granular skip: no gap, no resync needed")
check(r2b["frames"][1:] == [
    {"seq": 1, "offset": off_b, "length": 64, "status": "ok"},
    {"seq": 2, "offset": off_c, "length": 64, "status": "ok"}],
      "subsequent frames after in-span bad-payload still decoded")

# 2c: flip a seq byte only; length/extent stay legal -> bad-payload row,
# still located at exact byte range, no gap, subsequent frame continues.
s2c = bytearray(MAGIC + make_frame(0, pa) + make_frame(1, pb))
flip_off = 8 + 2
s2c[flip_off] ^= 0x80
r2c = parse_bytes(bytes(s2c))
check(r2c["frames"] == [
    {"seq": 0x00800000, "offset": 8, "length": 64, "status": "bad-payload"},
    {"seq": 1, "offset": 8 + HEADER_LEN + 64, "length": 64, "status": "ok"}],
    "header-field flip with legal extent -> bad-payload, exact range kept")
check(r2c["gaps"] == [], "no gap for an intact-extent damaged frame")

# Zero-length payload frame must parse fine.
r2d = parse_bytes(MAGIC + make_frame(9, b"") + make_frame(10, pb))
check(r2d["frames"][0] == {"seq": 9, "offset": 8, "length": 0,
                           "status": "ok"}, "zero-length payload frame ok")

# ---------------------------------------------------------------- test 3
print("test 3: mid-header and mid-payload truncation (byte level)")
base = MAGIC + make_frame(0, payload(0, 128)) + make_frame(1, payload(1, 128))
f1_header = 8 + (HEADER_LEN + 128)

# 3a: cut with 6 of 12 header bytes remaining.
cut_head = f1_header + 6
r3a = parse_bytes(base[:cut_head])
check(r3a["gaps"] == [{"offset": f1_header, "reason": "truncated"}],
      "mid-header truncation located at frame header offset")
check(r3a["frames"] == [{"seq": 0, "offset": 8, "length": 128,
                         "status": "ok"}], "frame before cut still decoded")

# 3b: cut 10 bytes into the payload.
cut_payload = f1_header + HEADER_LEN + 10
r3b = parse_bytes(base[:cut_payload])
check(r3b["gaps"] == [{"offset": f1_header, "reason": "truncated"}],
      "mid-payload truncation located at frame header offset")
check(r3b["regions"] == [{"start": 8, "end": f1_header, "frames": 1}],
      "truncated frame payload excluded from region")
check(r3b["summary"]["bytes_recovered"] == 128,
      "only complete ok payload bytes recovered")

# ---------------------------------------------------------------- test 4
print("test 4: random seq reordering gives identical region lists")
eq_payload = bytes(range(256))
frame_set = [make_frame(i, eq_payload) for i in range(12)]
rng = random.Random(20261114)
shuffle_a = frame_set[:]
rng.shuffle(shuffle_a)
shuffle_b = frame_set[:]
rng.shuffle(shuffle_b)
r4a = parse_bytes(MAGIC + b"".join(shuffle_a))
r4b = parse_bytes(MAGIC + b"".join(shuffle_b))
total_end = 8 + 12 * (HEADER_LEN + 256)
for tag, rpt in (("shuffle A", r4a), ("shuffle B", r4b)):
    check(rpt["regions"] == [{"start": 8, "end": total_end, "frames": 12}],
          f"{tag}: single region over all 12 frames")
    check(rpt["summary"]["frames_ok"] == 12 and rpt["gaps"] == [],
          f"{tag}: all frames ok regardless of seq order")
check(r4a["regions"] == r4b["regions"],
      "region list identical across random seq permutations")
# seq_gaps honestly records the physical-order discontinuities.
seqs_a = [f["seq"] for f in r4a["frames"]]
expected_breaks = sum(1 for x, y in zip(seqs_a, seqs_a[1:])
                      if y != (x + 1) & 0xFFFFFFFF)
check(len(r4a["seq_gaps"]) == expected_breaks,
      "seq_gaps count matches reordered stream truth")

# ---------------------------------------------------------------- test 5
print("test 5: determinism and arbitrary-start recovery")
r5_full = parse_bytes(bytes(stream))  # the test-1 damaged stream
check(parse_bytes(bytes(stream)) == r5_full,
      "repeat in-process parse is byte-identical")

body = bytes(stream)[len(MAGIC):]
boundaries = {0}
for f in r5_full["frames"]:
    boundaries.add(f["offset"] - len(MAGIC))
    boundaries.add(f["offset"] - len(MAGIC) + HEADER_LEN + f["length"])
for g in r5_full["gaps"]:
    boundaries.add(g["offset"] - len(MAGIC))
boundaries.add(len(body))

for rel in sorted(boundaries):
    suff = unframe.parse_stream(body[rel:], base=len(MAGIC) + rel)
    again = unframe.parse_stream(body[rel:], base=len(MAGIC) + rel)
    check(suff == again, f"deterministic suffix parse at rel={rel}")
    abs_start = len(MAGIC) + rel
    check(suff["frames"] == [f for f in r5_full["frames"]
                             if f["offset"] >= abs_start],
          f"suffix frames match full parse from rel={rel}")
    check(suff["gaps"] == [g for g in r5_full["gaps"]
                           if g["offset"] >= abs_start],
          f"suffix gaps match full parse from rel={rel}")

# Random interior starts: every reported frame must be a genuinely
# CRC-valid frame that also exists in the full parse (no fabrication);
# boundary starts above already prove exact suffix equivalence.
full_by_offset = {f["offset"]: f for f in r5_full["frames"]}
probe_rng = random.Random(42)
for _ in range(20):
    rel = probe_rng.randrange(len(body))
    suff = unframe.parse_stream(body[rel:], base=len(MAGIC) + rel)
    abs_start = len(MAGIC) + rel
    check(all(f["offset"] >= abs_start for f in suff["frames"]),
          f"interior start rel={rel}: no frame starts before start")
    check(all(full_by_offset.get(f["offset"]) == f for f in suff["frames"]),
          f"interior start rel={rel}: every frame exists in full parse")
    check(all(unframe._valid_frame_at(body, f["offset"] - len(MAGIC))
              is not None for f in suff["frames"]),
          f"interior start rel={rel}: every reported frame passes CRC")
    for region in suff["regions"]:
        covered = [f for f in r5_full["frames"]
                   if f["status"] == "ok"
                   and region["start"] <= f["offset"] < region["end"]]
        check(region["frames"] == len(covered)
              and region["end"] == covered[-1]["offset"] + HEADER_LEN
              + covered[-1]["length"],
              f"interior start rel={rel}: region maps onto full parse")

# Starting strictly inside a bad-header gap: resync without phantom data.
g = r5_full["gaps"][0]
inside = g["offset"] + 3
suff = unframe.parse_stream(bytes(stream)[inside:], base=inside)
check(suff["frames"][0]["offset"] == truth_offsets[5],
      "mid-gap start resyncs at next valid frame, no fabricated frame")

# ---------------------------------------------------------------- test 6
print("test 6: CLI contract and error exits")
in_path = tmp_path("good.zaf")
out_path = tmp_path("report.json")
extract_path = tmp_path("recovered.raw")
with open(in_path, "wb") as fh:
    fh.write(bytes(stream))
proc = run_cli([in_path, out_path, "--extract", extract_path], 0)
with open(out_path) as fh:
    cli_report = json.load(fh)
check(cli_report == r5_full, "CLI JSON equals in-process report")
with open(extract_path, "rb") as fh:
    check(fh.read() == expected_audio, "CLI --extract byte-identical audio")

missing = tmp_path("does-not-exist.zaf")
proc = run_cli([missing, out_path], 2)
check(b"magic" in proc.stderr, "missing file: stderr mentions magic")

bad_magic = tmp_path("bad.zaf")
with open(bad_magic, "wb") as fh:
    fh.write(b"NOTAZAF" + bytes(stream)[8:])
proc = run_cli([bad_magic, out_path], 2)
check(b"magic" in proc.stderr, "bad magic: stderr mentions magic")

all_bad = tmp_path("allbad.zaf")
all_bad_stream = bytearray(MAGIC + make_frame(0, payload(0, 32))
                           + make_frame(1, payload(1, 32)))
# One payload-bit flip per frame: extents stay legal, every CRC fails.
all_bad_stream[30] ^= 0xFF   # inside frame 0 payload (offsets 20..51)
all_bad_stream[70] ^= 0xFF   # inside frame 1 payload (offsets 64..95)
with open(all_bad, "wb") as fh:
    fh.write(bytes(all_bad_stream))
proc = run_cli([all_bad, out_path], 0)
with open(out_path) as fh:
    ab_report = json.load(fh)
check(ab_report["summary"]["frames_ok"] == 0
      and ab_report["summary"]["frames_total"] >= 1,
      "all frames bad but magic present: exit 0, frames_ok=0")

magic_only = tmp_path("magiconly.zaf")
with open(magic_only, "wb") as fh:
    fh.write(MAGIC)
proc = run_cli([magic_only, out_path], 0)
with open(out_path) as fh:
    mo = json.load(fh)
check(mo == {"frames": [], "gaps": [], "regions": [], "seq_gaps": [],
             "summary": {"frames_total": 0, "frames_ok": 0,
                         "frames_bad": 0, "gaps": 0,
                         "bytes_recovered": 0}},
      "magic-only stream: empty report, exit 0")

# ---------------------------------------------------------------- test 7
print("test 7: shipped make_demo.py end to end")
proc = subprocess.run([sys.executable, "-I", MAKE_DEMO], cwd=TMP,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
check(proc.returncode == 0, "make_demo.py runs")
demo_in = os.path.join(TMP, "demo.zaf")
demo_out = tmp_path("demo.json")
proc = run_cli([demo_in, demo_out], 0)
with open(demo_out) as fh:
    demo = json.load(fh)
check([(f["seq"], f["status"]) for f in demo["frames"]] ==
      [(0, "ok"), (1, "ok"), (2, "bad-payload"), (3, "ok")],
      "demo frame statuses: ok, ok, bad-payload, ok")
check(len(demo["gaps"]) == 1 and demo["gaps"][0]["reason"] == "truncated",
      "demo truncated tail reported once")
check(demo["regions"] == [
    {"start": 8, "end": 8 + (HEADER_LEN + 1024) + (HEADER_LEN + 800),
     "frames": 2},
    {"start": 8 + (HEADER_LEN + 1024) + (HEADER_LEN + 800)
     + (HEADER_LEN + 600),
     "end": 8 + (HEADER_LEN + 1024) + (HEADER_LEN + 800)
     + (HEADER_LEN + 600) + (HEADER_LEN + 400), "frames": 1}],
      "demo regions: [0,1] and [3]")
check(demo["summary"]["bytes_recovered"] == 1024 + 800 + 400,
      "demo bytes_recovered = 2224")

print(f"\n{FAIL_COUNT} failed, {PASS_COUNT} passed "
      f"(temp dir: {TMP})")
sys.exit(1 if FAIL_COUNT else 0)
