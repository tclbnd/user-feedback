import random
import struct
import sys

from zqframe.format import HEADER_SIZE, MAGIC
from zqframe.generator import (
    N_FRAMES, SAMPLES_PER_FRAME, build_clean, build_corrupted,
    make_payload, original_audio,
)
from zqframe.parser import (
    OK, BAD_HEADER, BAD_PAYLOAD, TRUNCATED,
    parse_stream, decode_intervals, intervals_from_frames,
)

FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (tag, name, (" -- " + detail) if detail else ""))
    if not cond:
        FAILURES.append(name)


def frame_status_table(result):
    return [(f["offset"], f["seq"], f["status"]) for f in result["frames"]]


def main():
    stream, truth = build_corrupted()
    size = truth["frame_size"]
    offs = truth["offsets"]

    # ---- Acceptance 1a: per-frame status table matches generator truth ----
    res = parse_stream(stream)
    got = {i: None for i in range(N_FRAMES)}
    for f in res["frames"]:
        idx = f["offset"] // size
        got[idx] = f["status"]
    exp = truth["expected_status"]
    check("1a per-frame status table matches truth", got == exp,
          "got=%s" % got)

    # Bad frames located at exact byte ranges
    gap_set = set(res["gaps"])
    exp_gaps = set()
    for c in truth["corruptions"]:
        o = offs[c["frame"]]
        exp_gaps.add((o, o + size))
    check("1a bad-frame byte ranges exact", exp_gaps <= gap_set,
          "exp=%s got=%s" % (sorted(exp_gaps), sorted(gap_set)))

    # seq discontinuities recorded honestly: 2->4, 5->7, 8->10
    check("1a seq gaps recorded",
          res["seq_gaps"] == [(2, 4), (5, 7), (8, 10)],
          str(res["seq_gaps"]))

    # ---- Acceptance 1b: decodable audio sample-identical to original ----
    orig = original_audio()
    ok_audio = True
    decoded_total = b""
    for (s, e), payload in decode_intervals(stream, res):
        # interval [s,e) covers whole frames; expected audio is the
        # concatenation of the original payloads of those frames
        expect = b"".join(make_payload(o // size) for o in range(s, e, size))
        if payload != expect:
            ok_audio = False
        decoded_total += payload
    # sample-level comparison
    got_samples = struct.unpack("<%dh" % (len(decoded_total) // 2), decoded_total)
    exp_frames = [i for i in range(N_FRAMES) if exp[i] == OK]
    exp_samples = []
    for i in exp_frames:
        p = make_payload(i)
        exp_samples.extend(struct.unpack("<%dh" % SAMPLES_PER_FRAME, p))
    check("1b decoded audio sample-identical",
          ok_audio and list(got_samples) == exp_samples,
          "%d samples" % len(got_samples))

    # ---- Acceptance 1c: repeat parse identical; arbitrary restart identical ----
    res2 = parse_stream(stream)
    check("1c repeat parse identical",
          frame_status_table(res) == frame_status_table(res2)
          and res["intervals"] == res2["intervals"])

    restart_ok = True
    rng = random.Random(1234)
    for _ in range(200):
        cut = rng.randrange(0, len(stream))
        sub = parse_stream(stream[cut:], base_offset=cut)
        # every ok frame found from restart must appear identically in
        # the full parse (same offset, seq, status)
        full_by_off = {f["offset"]: f for f in res["frames"]}
        for f in sub["frames"]:
            g = full_by_off.get(f["offset"])
            if f["status"] == OK:
                if g is None or g["status"] != OK or g["seq"] != f["seq"]:
                    restart_ok = False
            else:
                # non-ok frames from a mid-frame restart may differ in
                # extent; only require they do not fabricate ok data
                pass
        # intervals of the restart parse must be consistent with the
        # full parse: each restart interval is either identical to a
        # full-parse interval or a suffix sub-interval of one (a restart
        # landing inside a frame can only lose whole-frame prefixes,
        # never invent or shift frame boundaries).
        for iv in sub["intervals"]:
            if not any(iv[0] >= fi[0] and iv[1] <= fi[1]
                       for fi in res["intervals"]):
                restart_ok = False
        # and every full-parse interval starting at or after the first
        # complete frame visible to the restart must be reproduced
        first_ok = min((f["offset"] for f in sub["frames"]
                        if f["status"] == OK), default=None)
        if first_ok is not None:
            for fi in res["intervals"]:
                if fi[0] >= first_ok and fi not in sub["intervals"]:
                    restart_ok = False
    check("1c arbitrary restart consistent", restart_ok)

    # ---- Acceptance 2a: truncation mid-header and mid-payload ----
    clean_frames, clean = build_clean()
    # cut in the middle of frame 5 header
    cut_h = offs[5] + 7
    rt = parse_stream(clean[:cut_h])
    last = rt["frames"][-1]
    check("2a mid-header truncate located",
          last["status"] == TRUNCATED and last["offset"] == offs[5]
          and last["header_bytes"] == 7 and rt["gaps"][-1] == (cut_h - 7, cut_h),
          str(last))
    # cut in the middle of frame 5 payload
    cut_p = offs[5] + HEADER_SIZE + 30
    rt = parse_stream(clean[:cut_p])
    last = rt["frames"][-1]
    check("2a mid-payload truncate located",
          last["status"] == TRUNCATED and last["offset"] == offs[5]
          and last["seq"] == 5 and rt["gaps"][-1] == (offs[5], cut_p),
          str(last))

    # ---- Acceptance 2b: randomly reordered reads give same intervals ----
    base_intervals = res["intervals"]
    reorder_ok = True
    rng = random.Random(99)
    for _ in range(50):
        fr = list(res["frames"])
        rng.shuffle(fr)
        if intervals_from_frames(fr) != base_intervals:
            reorder_ok = False
    check("2b reordered reads -> identical intervals", reorder_ok)

    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), FAILURES))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
