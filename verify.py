#!/usr/bin/env python3
"""Acceptance harness for the RTP/H.264 depacketizer.

Builds packet fixtures with make_demo, runs depacketize.py on them and
checks the results against the generator truth:

1. Lossless capture crossing the 16-bit sequence and 32-bit timestamp
   wraparounds with injected duplicates and reordering: per-NAL hashes
   and extended access-unit timestamps must match the truth exactly.
2. FU-A middle-fragment loss and IDR parameter-set (STAP-A) loss, each
   separately and combined: no partial unit may be output, recovery
   must happen at the next complete IDR carrying matching SPS/PPS, and
   the pending queue must never exceed the window.
3. A synthetic capture exercising late packets, duplicates of retired
   packets, malformed payloads and EOF truncation.
4. Independent validation of the recovered pictures: bit-exact NAL
   comparison against the source plus an H.264 slice-header front-end
   parse (SPS/PPS resolution, first_mb_in_slice, frame_num).  When an
   ffmpeg binary is available a full pixel-level decode comparison is
   run as well; bit-exact NAL recovery already implies identical
   decoded frames for any conformant decoder.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import make_demo

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source.h264"
DEPACKETIZE = ROOT / "depacketize.py"

FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def annexb_nals(data):
    """Independent Annex B splitter (shares no code with depacketize)."""
    return [p for p in re.split(rb"\x00\x00(?:\x00)?\x01", data) if p]


def run_depacketize(tmp, name, log):
    packets = tmp / (name + ".json")
    video = tmp / (name + ".h264")
    report = tmp / (name + ".report.json")
    packets.write_text(json.dumps(log) + "\n")
    subprocess.run([sys.executable, str(DEPACKETIZE), str(packets),
                    str(video), str(report)], check=True)
    return json.loads(report.read_text()), video.read_bytes()


# ---------------------------------------------------------------------
# minimal H.264 front-end used as the independent decoder stand-in
# ---------------------------------------------------------------------
def _rbsp(nal):
    out = bytearray()
    zeros = 0
    for byte in nal[1:]:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


class _Bits:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def u(self, n):
        value = 0
        for _ in range(n):
            value = value << 1 | (self.data[self.pos // 8] >> (7 - self.pos % 8)) & 1
            self.pos += 1
        return value

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + self.u(zeros)


def check_pictures(aus):
    """Parse every recovered access unit like a decoder front-end:
    SPS/PPS must resolve and each VCL slice must start at MB 0 with a
    known picture parameter set.  Returns (ok, detail)."""
    sps, pps = {}, {}
    frames = 0
    for index, au in enumerate(aus):
        vcl = 0
        for nal in au:
            kind = nal[0] & 31
            try:
                if kind == 7:
                    bits = _Bits(_rbsp(nal))
                    bits.u(24)
                    sps_id = bits.ue()
                    sps[sps_id] = bits.ue()  # log2_max_frame_num_minus4
                elif kind == 8:
                    bits = _Bits(_rbsp(nal))
                    pps_id, sps_id = bits.ue(), bits.ue()
                    if sps_id not in sps:
                        return False, "au %d: pps %d references unknown sps %d" % (
                            index, pps_id, sps_id)
                    pps[pps_id] = sps_id
                elif kind in (1, 5):
                    vcl += 1
                    bits = _Bits(_rbsp(nal))
                    first_mb = bits.ue()
                    bits.ue()  # slice_type
                    pps_id = bits.ue()
                    if first_mb != 0:
                        return False, "au %d: slice does not start picture" % index
                    if pps_id not in pps:
                        return False, "au %d: slice uses unknown pps %d" % (index, pps_id)
                    bits.u(sps[pps[pps_id]] + 4)  # frame_num
                    if kind == 5:
                        bits.ue()  # idr_pic_id
            except (IndexError, ValueError, KeyError) as exc:
                return False, "au %d: unparseable slice header (%s)" % (index, exc)
        if vcl < 1:
            return False, "au %d: no picture" % index
        frames += 1
    return True, "%d pictures parsed" % frames


def ffmpeg_compare(video, source, expected_indexes):
    """Pixel-level frame comparison, only when ffmpeg is available."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("SKIP ffmpeg not available: pixel decode comparison skipped "
              "(bit-exact NAL equality already checked)")
        return
    def frame_md5s(path):
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "framehash", "-"],
            check=True, capture_output=True, text=True).stdout
        return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines()
                if line.startswith("0,")]
    got = frame_md5s(video)
    want_all = frame_md5s(source)
    want = [want_all[i] for i in expected_indexes]
    check(got == want, "ffmpeg pixel comparison: %d recovered frames match "
          "source frames %s" % (len(got), expected_indexes))


# ---------------------------------------------------------------------
# fixture cases
# ---------------------------------------------------------------------
def case(tmp, name, faults, expected_indexes, expect):
    raw = SOURCE.read_bytes()
    log, source, base = make_demo.packetize(raw, faults)
    truth = make_demo.truth(source, base)
    report, video = run_depacketize(tmp, name, log)
    stats = report["stats"]

    want_units = [truth["units"][i] for i in expected_indexes]
    check(report["units"] == want_units,
          "%s: units (extended timestamps + per-NAL hashes) match truth" % name)
    want_nals = [nal for i in expected_indexes for nal in source[i]]
    check(annexb_nals(video) == want_nals,
          "%s: Annex B output is bit-exact with source NALs" % name)
    for key, value in expect.items():
        check(stats.get(key) == value, "%s: stats %s == %r (got %r)" % (
            name, key, value, stats.get(key)))
    check(stats["max_pending"] <= log["window"],
          "%s: pending queue %d never exceeds window %d" % (
              name, stats["max_pending"], log["window"]))
    dropped_ts = [d["timestamp"] for d in report["dropped"]]
    want_dropped = [truth["units"][i]["timestamp"] for i in range(len(truth["units"]))
                    if i not in expected_indexes]
    check(dropped_ts == want_dropped,
          "%s: dropped units are exactly %s with reasons" % (name, want_dropped))
    check(all(d["reason"] for d in report["dropped"]),
          "%s: every dropped unit carries a reason" % name)

    flat = annexb_nals(video)
    aus = []
    pos = 0
    for i in expected_indexes:
        aus.append(flat[pos:pos + len(source[i])])
        pos += len(source[i])
    ok, detail = check_pictures(aus)
    check(ok, "%s: independent front-end parse of recovered pictures (%s)" % (name, detail))
    ffmpeg_compare(tmp / (name + ".h264"), SOURCE, expected_indexes)
    return report


def synthetic_case(tmp):
    """Hand-built capture: late packet, retired duplicate, malformed
    payloads, EOF truncation, untrusted-then-recovered output."""
    raw = SOURCE.read_bytes()
    src = make_demo.units(raw)
    aud, sps, pps = src[4][0], src[4][1], src[4][2]
    idr = src[4][3]
    slice1, slice2, slice3 = src[1][1], src[2][1], src[3][1]

    def pkt(seq, ts, marker, payload):
        return {"sequence": seq % 65536, "timestamp": ts, "marker": marker,
                "hex": payload.hex() if isinstance(payload, bytes) else payload}

    bad_stap = b"\x78" + b"\x00\x05ab"          # declared length 5, only 2 bytes
    fu_r_bit = bytes([(aud[0] & 0xE0) | 28, 0x20 | 1]) + b"xx"  # R bit set
    packets = [
        pkt(100, 10, False, aud), pkt(101, 10, False, sps),
        pkt(102, 10, False, pps), pkt(103, 10, True, idr),      # AU0: recovery IDR
        pkt(105, 30, False, aud), pkt(106, 30, True, slice2),   # AU2 before AU1!
        pkt(107, 40, False, aud),
        pkt(109, 50, False, aud),                               # seals 104 as lost
        pkt(104, 20, True, slice1),                             # late arrival
        pkt(108, 40, True, slice3),                             # completes AU3
        pkt(108, 40, True, slice3),                             # duplicate of retired
        pkt(110, 50, False, sps), pkt(111, 50, False, pps),
        pkt(112, 50, True, idr),                                # AU4: recovery IDR
        pkt(113, 60, False, ""),                                # empty payload
        pkt(114, 60, False, bad_stap),                          # bad aggregation
        pkt(115, 60, True, slice1),                             # closes broken AU5
        pkt(116, 70, True, fu_r_bit),                           # reserved bit set
        pkt(117, 80, False, aud),                               # EOF truncation
    ]
    log = {"first_sequence": 100, "window": 4, "packets": packets}
    report, video = run_depacketize(tmp, "synthetic", log)
    stats = report["stats"]

    check([u["timestamp"] for u in report["units"]] == [10, 50],
          "synthetic: only the two recovery IDR units are output")
    check(annexb_nals(video) == [aud, sps, pps, idr, aud, sps, pps, idr],
          "synthetic: Annex B output contains exactly the two IDR units")
    check(stats["lost"] == 1, "synthetic: lost == 1 (got %d)" % stats["lost"])
    check(stats["late"] == 1, "synthetic: late == 1 (got %d)" % stats["late"])
    check(stats["duplicates"] == 1,
          "synthetic: duplicates == 1 (got %d)" % stats["duplicates"])
    check(stats["malformed"] == 3,
          "synthetic: malformed == 3 (got %d)" % stats["malformed"])
    check(stats["max_pending"] <= 4,
          "synthetic: pending queue %d within window 4" % stats["max_pending"])
    reasons = " | ".join(d["reason"] for d in report["dropped"])
    check(len(report["dropped"]) == 5 and "untrusted" in reasons
          and "malformed" in reasons and "truncated" in reasons,
          "synthetic: dropped reasons cover untrusted/malformed/truncated")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="depacketize-verify-"))
    print("fixtures in", tmp)

    print("\n== case A: lossless, sequence/timestamp wraparound, dup + reorder ==")
    report, _ = None, None
    raw = SOURCE.read_bytes()
    log, source, base = make_demo.packetize(raw)
    n_dup = len(log["packets"]) - len({p["sequence"] for p in log["packets"]})
    case(tmp, "lossless", set(), list(range(12)),
         {"lost": 0, "late": 0, "malformed": 0, "duplicates": n_dup})

    print("\n== case B: FU-A middle fragment lost (access unit 1) ==")
    case(tmp, "fu-middle", {"fu-middle"}, [0] + list(range(4, 12)),
         {"lost": 1, "malformed": 1})

    print("\n== case C: IDR parameter sets lost (STAP-A of access unit 4) ==")
    case(tmp, "stap", {"stap"}, list(range(4)) + list(range(8, 12)),
         {"lost": 1, "malformed": 0})

    print("\n== case D: both faults ==")
    case(tmp, "both", {"fu-middle", "stap"}, [0] + list(range(8, 12)),
         {"lost": 2, "malformed": 1})

    print("\n== case E: synthetic late/duplicate/malformed/EOF ==")
    synthetic_case(tmp)

    print()
    if FAILURES:
        print("%d FAILURES" % len(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
