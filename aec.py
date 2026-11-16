#!/usr/bin/env python3
"""Frame-energy AEC control logic.

CLI: python3 aec.py input.json output.json

Input:  {"frames": [{"t", "farend", "nearend", "mic"} ...], "win": W}
Output: {"frames": [{"t", "state", "used_est", "ser"} ...],
         "alerts": [{"t", "kind": "delay-shift"} ...]}

Rules (see README.md):
  est = 0.7 * max(farend) over the last W ms (inclusive)
  ser = mic / max(est, 1e-9)
  * ser >= 0.5 (or reference silence) -> freeze adaptation and HOLD the
    previous used_est; the current frame estimate is never published while
    frozen.
  * A double-talk freeze persists while nearend > 0.3 * est_prev.  Once
    nearend drops to/below that level the frame is still served frozen, and
    adaptation resumes on the NEXT frame (hysteresis / agreed resume state).
  * While adapting, an est jump of more than 40% relative to the previous
    used_est is reported as a delay-shift and used_est resyncs immediately;
    the state stays "adapting".
"""
import json
import sys

REF_SILENCE = 0.0       # zero far-end energy = reference silence
DT_SER = 0.5            # ser at/above this marks double-talk
NEAR_HYST = 0.3         # frozen while nearend > 0.3 * est_prev
DELAY_JUMP = 0.4        # relative est jump > 40% => delay-shift
EST_FLOOR = 1e-9


def process(doc):
    W = doc["win"]
    frames = doc["frames"]
    out = []
    alerts = []

    frozen = False          # currently serving frozen frames
    resume_pending = False  # double-talk: resume on the next frame
    dt_entry = False        # the active freeze was entered as double-talk
    silence_entry = False   # the active freeze was entered via silence
    used = 0.0              # last published used_est (held while frozen)
    est_prev = 0.0          # est of the previous frame

    for f in frames:
        t = f["t"]
        past = [g for g in frames if t - W <= g["t"] <= t]
        est = 0.7 * max((g["farend"] for g in past), default=0.0)
        ser = f["mic"] / max(est, EST_FLOOR)
        ref_silent = est <= REF_SILENCE

        # Resume rules at a frame boundary:
        #  * double-talk freeze: an armed resume takes effect on this frame;
        #  * silence freeze: the first active reference frame ends it
        #    immediately (the agreed "continue on recovery" state).
        if resume_pending and not ref_silent:
            frozen = False
            resume_pending = False
        elif silence_entry and not ref_silent:
            frozen = False
            silence_entry = False

        if frozen:
            # Double-talk hysteresis: only a freeze actually entered through
            # the double-talk branch may arm the one-frame delayed resume.
            if (dt_entry and not ref_silent
                    and f["nearend"] <= NEAR_HYST * est_prev):
                resume_pending = True
            state = "frozen"
        elif ref_silent or ser >= DT_SER:
            # Double-talk entry or reference silence: freeze and keep the
            # previously published estimate (est is not adopted here).
            frozen = True
            dt_entry = (not ref_silent) and ser >= DT_SER
            silence_entry = ref_silent
            state = "frozen"
        else:
            # Adapting.  Detect a bulk-delay jump against the last
            # published estimate; a cold start (used == 0) cannot jump.
            dt_entry = False
            silence_entry = False
            if used > 0.0 and abs(est - used) > DELAY_JUMP * used:
                alerts.append({"t": t, "kind": "delay-shift"})
            used = est
            state = "adapting"

        out.append({
            "t": t,
            "state": state,
            "used_est": round(used, 2),
            "ser": round(ser, 3),
        })
        est_prev = est

    return {"frames": out, "alerts": alerts}


def main():
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
    result = process(doc)
    with open(sys.argv[2], "w") as fh:
        json.dump(result, fh, indent=1)


if __name__ == "__main__":
    main()
