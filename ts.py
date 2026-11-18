#!/usr/bin/env python3
"""MPEG-TS clock recovery: unwrap 33-bit PCR/PTS counters onto a continuous
64-bit timeline, keeping an independent unwrap epoch per program."""
import json, sys

M = 1 << 33      # 33-bit counter modulus (90 kHz ticks)
HALF = 1 << 32   # half modulus: wrap / discontinuity threshold


def main():
    d = json.load(open(sys.argv[1]))
    out = []
    programs = {}   # program -> {"epoch", "last_pcr", "last_pcr64"}
    discs = []
    last_prog = None

    for i, p in enumerate(d["packets"]):
        prog = p["program"]
        # A changed program is only logged: each program keeps its OWN
        # epoch, so the new program is never stitched onto the old clock.
        if last_prog is not None and prog != last_prog:
            discs.append({"index": i, "kind": "program_switch",
                          "detail": "%s->%s" % (last_prog, prog)})
        last_prog = prog
        st = programs.setdefault(prog, {"epoch": 0, "last_pcr": None,
                                        "last_pcr64": None})

        rec = {"pts64": None, "pcr64": None, "ms": None}

        if p["pcr"] is not None:
            raw = p["pcr"]
            if st["last_pcr"] is not None:
                delta = raw - st["last_pcr"]
                if delta < -HALF:
                    # counter wrapped past 2^33: advance one modulus.
                    st["epoch"] += M
                    discs.append({"index": i, "kind": "wrap",
                                  "detail": "pcr wrap (program %s)" % prog})
                elif delta > HALF:
                    # forward raw jump > half modulus == backward time jump
                    # without wrap (source restarted). Re-baseline on this
                    # pcr while keeping the program timeline monotonic.
                    st["epoch"] = st["last_pcr64"] - raw
                    discs.append({"index": i, "kind": "reset",
                                  "detail": "pcr reset (program %s)" % prog})
            st["last_pcr"] = raw
            st["last_pcr64"] = raw + st["epoch"]
            rec["pcr64"] = st["last_pcr64"]
            rec["ms"] = round(rec["pcr64"] / 90, 3)

        if p["pts"] is not None:
            # Unwrap against the SAME program epoch. A pts may legitimately
            # sit slightly BEFORE the current pcr (late group racing a
            # wrap): allow up to one wrap-half of lookahead/lookbehind vs
            # the program's reference clock. Missing source timestamps stay
            # null -- never invented.
            pts64 = p["pts"] + st["epoch"]
            ref = rec["pcr64"] if rec["pcr64"] is not None else st["last_pcr64"]
            if ref is not None:
                if pts64 - ref > HALF:
                    pts64 -= M
                elif ref - pts64 > HALF:
                    pts64 += M
            rec["pts64"] = pts64
            if rec["ms"] is None:
                rec["ms"] = round(pts64 / 90, 3)

        out.append(rec)

    prog_out = {str(k): {"epoch": v["epoch"], "last_pcr": v["last_pcr"]}
                for k, v in programs.items()}
    json.dump({"packets": out, "programs": prog_out, "discontinuities": discs},
              open(sys.argv[2], "w"))


main()
