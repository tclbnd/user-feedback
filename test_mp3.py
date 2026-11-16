#!/usr/bin/env python3
# Acceptance tests for the MP3 VBR seek planner (mp3.py).
# Covers: CBR, VBR, first/last targets, cross-frame reservoir
# references, seeks adjacent to corrupt frames, EOF targets.
import json, os, subprocess, sys

TMP = "/private/tmp/zq-single-pilot-fe23sz26/A29/tmp"
IN = os.path.join(TMP, "mp3_in.json")
OUT = os.path.join(TMP, "mp3_out.json")

def F(ln, s, mb, v=True):
    return dict(len=ln, samples=s, main_begin=mb, valid=v)

def run(frames, tag, target):
    with open(IN, "w") as fh:
        json.dump(dict(frames=frames, tag_bytes=tag, target=target), fh)
    subprocess.check_call([sys.executable, "mp3.py", IN, OUT],
                          cwd=os.path.dirname(os.path.abspath(__file__)))
    with open(OUT) as fh:
        return json.load(fh)

def plan(sf, bo, d, rel, notes):
    return dict(start_frame=sf, byte_offset=bo, discard=d,
                reliable=rel, notes=notes)

OK = ["reservoir-ok"]
BAD = ["reservoir-broken"]
cases = []

# 1. CBR + tag: first sample, mid-frame, frame boundary, last sample
cbr = [F(200, 1152, 0)] * 4
cases += [
    ("cbr first sample",   cbr, 128, 0,    plan(0, 128, 0,    True, OK)),
    ("cbr mid frame0",     cbr, 128, 1151, plan(0, 128, 1151, True, OK)),
    ("cbr frame boundary", cbr, 128, 1152, plan(1, 328, 0,    True, OK)),
    ("cbr last sample",    cbr, 128, 4607, plan(3, 728, 1151, True, OK)),
]

# 2. VBR: average-bitrate estimate lands on the wrong frame
vbr = [F(100, 576, 0), F(400, 1728, 0), F(150, 1152, 0), F(150, 1152, 0)]
cases += [
    ("vbr first sample",   vbr, 0, 0,    plan(0, 0,   0,    True, OK)),
    ("vbr exact locate",   vbr, 0, 1000, plan(1, 100, 424,  True, OK)),
    ("vbr last sample",    vbr, 0, 4607, plan(3, 650, 1151, True, OK)),
]

# 3. Cross-frame reservoir: must pre-read from frame 0
res = [F(300, 1152, 0), F(100, 1152, 250), F(100, 1152, 50)]
cases += [
    ("reservoir deep walk", res, 0, 2314, plan(0, 0, 2314, True, OK)),
    ("reservoir one back",  res, 0, 1200, plan(0, 0, 1200, True, OK)),
    ("reservoir none",      res, 0, 10,   plan(0, 0, 10,   True, OK)),
]

# 4. Corrupt frame immediately before target: chain broken
brk = [F(500, 1152, 0), F(200, 1152, 0, False), F(100, 1152, 300)]
cases += [
    ("corrupt barrier",      brk, 0, 1152, plan(2, 700, 0, False, BAD)),
    ("corrupt barrier head", brk, 0, 0,    plan(0, 0,   0, True, OK)),
]

# 5. Corrupt frame behind a workable window; leading frames w/o reservoir
lead = [F(50, 1152, 0, False), F(300, 1152, 0), F(100, 1152, 200)]
cases += [
    ("walk stops at corrupt", lead, 10, 1200, plan(1, 60, 1200, True, OK)),
    ("first valid after bad", lead, 10, 0,    plan(1, 60, 0,  True, OK)),
]

# 6. EOF targets (past last valid sample)
cases += [
    ("eof reachable", res, 0, 5000,   plan(0, 0,   5000, True,  OK + ["target-eof"])),
    ("eof broken",    brk, 0, 9999,   plan(2, 700, 8847, False, BAD + ["target-eof"])),
    ("eof cbr",       cbr, 128, 4608, plan(3, 728, 1152, True,  OK + ["target-eof"])),
]

# 7. Truncated stream start: frame 0 itself needs reservoir bytes
cases += [
    ("truncated start", [F(100, 1152, 40)], 0, 5, plan(0, 0, 5, False, BAD)),
]

# 8. Boundary target whose frame needs one frame of pre-read
bnd = [F(200, 1152, 0), F(100, 1152, 150)]
cases += [
    ("boundary preread", bnd, 0, 1152, plan(0, 0, 1152, True, OK)),
]

fails = 0
for name, frames, tag, tgt, want in cases:
    got = run(frames, tag, tgt)
    if got == want:
        print("PASS  " + name)
    else:
        fails += 1
        print("FAIL  " + name)
        for k in want:
            if got.get(k) != want[k]:
                print("    " + k + ": want " + str(want[k])
                      + " got " + str(got.get(k)))
print("----")
print(str(len(cases) - fails) + "/" + str(len(cases)) + " passed")
sys.exit(1 if fails else 0)