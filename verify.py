"""Acceptance checks for the RTP/H.264 depacketizer.

Check 1 (lossless fixture): the packet log spans the 16-bit sequence and
32-bit timestamp wraparounds and carries injected duplicates/reordering.
Every access unit's per-NAL SHA-256 and extended timestamp must equal the
generator ground truth.

Check 2 (fault fixture): an FU-A middle fragment and the SPS/PPS STAP-A of
an IDR frame are dropped. No partial fragment may be emitted, output must
stay silent until the next complete IDR with matching parameter sets, the
recovered continuous pictures are compared frame by frame against an
independent decoder (ffmpeg when available; otherwise byte-exact Annex B
comparison against the source frames, which is decode-equivalent), and the
pending queue must never exceed the configured window.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import make_demo

ROOT = Path(__file__).resolve().parent
RAW = (ROOT / "source.h264").read_bytes()
TS_STEP = 1500


def split_annexb(data):
    return [p for p in re.split(rb"\x00\x00(?:\x00)?\x01", data) if p]


def sha(nal):
    return hashlib.sha256(nal).hexdigest()


def run_depacketizer(packet_obj, tag):
    pin = ROOT / ("%s_packets.json" % tag)
    vout = ROOT / ("%s_recovered.h264" % tag)
    rout = ROOT / ("%s_report.json" % tag)
    pin.write_text(json.dumps(packet_obj) + "\n")
    subprocess.run([sys.executable, str(ROOT / "depacketize.py"),
                    str(pin), str(vout), str(rout)], check=True)
    return json.loads(rout.read_text()), vout.read_bytes()


def truth(source, base):
    return [(base + i * TS_STEP, [sha(n) for n in au]) for i, au in enumerate(source)]


def check_lossless():
    packets, source, base = make_demo.packetize(RAW, faults=False)
    window = packets["window"]
    report, recovered = run_depacketizer(packets, "noloss")
    stats = report["stats"]
    arrivals = len(packets["packets"])
    unique = len({p["sequence"] for p in packets["packets"]})

    assert stats["lost"] == 0, stats
    assert stats["late"] == 0, stats
    assert stats["malformed"] == 0, stats
    assert stats["duplicates"] == arrivals - unique, stats
    assert stats["max_pending"] <= window, stats
    assert not report["drops"], report["drops"]

    expected = truth(source, base)
    units = report["units"]
    assert len(units) == len(expected), (len(units), len(expected))
    for unit, (ts, hashes) in zip(units, expected):
        assert unit["timestamp"] == ts, (unit["timestamp"], ts)
        assert unit["nals"] == hashes, "NAL hash mismatch at ts %d" % ts
    # extended timestamps really did cross the 32-bit wraparound
    assert min(u["timestamp"] for u in units) < 2 ** 32
    assert max(u["timestamp"] for u in units) >= 2 ** 32
    # and the 16-bit sequence space wrapped as well
    seqs = sorted({p["sequence"] for p in packets["packets"]})
    assert seqs[0] == 0 and seqs[-1] == 65535
    # Annex B output carries exactly the source NAL stream, in order
    flat = [n for au in source for n in au]
    assert split_annexb(recovered) == flat
    print("check 1 (lossless wraparound/dup/reorder): PASS "
          "(%d units, stats=%s)" % (len(units), stats))


def independent_frame_compare(recovered, source, kept):
    """Compare recovered pictures against an independent decoder, frame by frame."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        ref = ROOT / "fault_reference.h264"
        ref.write_bytes(b"".join(b"\x00\x00\x00\x01" + n
                                 for i in kept for n in source[i]))
        for name, path in (("recovered", ROOT / "fault_recovered.h264"),
                           ("reference", ref)):
            out = ROOT / ("fault_%s.yuv" % name)
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i",
                            str(path), "-f", "rawvideo", str(out)], check=True)
        got = (ROOT / "fault_recovered.yuv").read_bytes()
        want = (ROOT / "fault_reference.yuv").read_bytes()
        assert got == want and got, "decoded pictures differ"
        return "ffmpeg decoded %d frames, bit-exact" % len(kept)
    # No decoder in this sandbox: byte-identical Annex B access units are
    # decode-equivalent for any conforming decoder.
    expected = b"".join(b"\x00\x00\x00" + b"\x01" + n for i in kept for n in source[i])
    assert recovered == expected, "recovered stream is not byte-identical"
    return "no ffmpeg available; Annex B bytes identical to source frames %s" % (kept,)


def check_faults():
    packets, source, base = make_demo.packetize(RAW, faults=True)
    window = packets["window"]
    report, recovered = run_depacketizer(packets, "fault")
    stats = report["stats"]
    expected = truth(source, base)

    kept = [0, 8, 9, 10, 11]          # AU0, then silence until the IDR at AU8
    dropped = [1, 2, 3, 4, 5, 6, 7]
    units = report["units"]
    assert [u["timestamp"] for u in units] == [expected[i][0] for i in kept]
    for unit, i in zip(units, kept):
        assert unit["nals"] == expected[i][1], "corrupted AU %d emitted" % i
    # every dropped access unit is accounted for with a reason
    assert [d["timestamp"] for d in report["drops"]] == [expected[i][0] for i in dropped]
    assert all(d["reason"] for d in report["drops"])
    # no partial FU-A fragment or orphan NAL leaked into the output
    valid = {sha(n) for au in source for n in au}
    for unit in units:
        for h in unit["nals"]:
            assert h in valid, "unknown NAL emitted"
    # exactly the two generator-dropped packets are lost; queue stayed bounded
    assert stats["lost"] == 2, stats
    assert stats["malformed"] == 0, stats
    assert stats["late"] == 0, stats
    assert stats["max_pending"] <= window, stats
    note = independent_frame_compare(recovered, source, kept)
    print("check 2 (FU-A middle + IDR parameter set loss): PASS "
          "(kept AUs %s, drops=%d, stats=%s; %s)"
          % (kept, len(report["drops"]), stats, note))


if __name__ == "__main__":
    check_lossless()
    check_faults()
    print("all acceptance checks passed")
