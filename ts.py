#!/usr/bin/env python3
"""Offline MPEG-TS PCR/PTS clock recovery.

Input packets are ordered records of the supported PAT/PMT/PCR/PES
subset: ``{"pid", "program", "pcr": int|null, "pts": int|null}`` where
pcr/pts are raw 33-bit counter ticks (90 kHz, modulus 2**33).

Each program owns an independent unwrap epoch so that interleaved
programs, program-map switches, counter wraps, source restarts and
late packets can never cross-contaminate one another.  See README.md
for the full rule set and output contract.
"""
import json
import sys

MOD = 1 << 33        # 33-bit counter modulus
HALF = 1 << 32       # half modulus: wrap / discontinuity threshold
TICKS_PER_MS = 90    # 90 kHz clock -> 1 ms is 90 ticks


class ProgramClock:
    """Unwrap state for a single program's independent timeline."""

    def __init__(self, name):
        self.name = name
        self.epoch = 0             # offset added to this program's raw ticks
        self.last_raw_pcr = None   # raw PCR of the previous PCR packet
        self.last_pcr64 = None     # last unwrapped PCR on this timeline
        self.last_pts64 = None     # last unwrapped PTS (anchor pre-first-PCR)

    def unwrap_pcr(self, raw):
        """Unwrap a raw PCR value.

        Returns ``(pcr64, event_kind)`` where event_kind is ``"wrap"``,
        ``"reset"`` or ``None``.  A small backward step (late packet
        arriving out of order) produces no event and no epoch change;
        only jumps larger than one wrap-half are treated as wrap or
        source restart.
        """
        if self.last_raw_pcr is None:
            # First PCR of the program establishes its own baseline;
            # it is never aligned to another program's clock.
            pcr64 = raw + self.epoch
            self.last_raw_pcr = raw
            self.last_pcr64 = pcr64
            return pcr64, None

        delta = raw - self.last_raw_pcr
        if delta < -HALF:
            # Backward jump over half the modulus: the 33-bit counter
            # wrapped. Lift the timeline by one full modulus.
            self.epoch += MOD
            kind = "wrap"
        elif delta > HALF:
            # Forward jump over half the modulus: a backward
            # discontinuity without wrap (e.g. source restarted).
            # Re-baseline the epoch onto the current timeline so the
            # unwrapped output never jumps backwards on a reset.
            self.epoch = self.last_pcr64 - raw
            # Pre-restart PTS anchors are meaningless on the new baseline.
            self.last_pts64 = None
            kind = "reset"
        else:
            # Ordinary progression, including small backward steps of
            # late packets: leave the epoch untouched.
            kind = None

        pcr64 = raw + self.epoch
        self.last_raw_pcr = raw
        self.last_pcr64 = pcr64
        return pcr64, kind

    def unwrap_pts(self, raw):
        """Unwrap a raw PTS against this program's current epoch.

        A PTS may legitimately sit slightly before the current PCR
        (up to one wrap-half): when PCR has already crossed the wrap
        but the PTS still carries the pre-wrap raw value, pull it back
        by one modulus instead of inventing a forward jump.
        """
        pts64 = raw + self.epoch
        ref = self.last_pcr64
        if ref is None:
            ref = self.last_pts64
        if ref is not None and pts64 - ref > HALF:
            pts64 -= MOD
        self.last_pts64 = pts64
        return pts64


def recover(packets):
    """Run per-program clock recovery over ordered packet records."""
    clocks = {}
    out = []
    discontinuities = []
    prev_program = None

    for index, pkt in enumerate(packets):
        program = pkt["program"]
        clock = clocks.get(program)
        if clock is None:
            # Lazily create the program's own timeline; its epoch
            # starts at zero regardless of what other programs did.
            clock = ProgramClock(program)
            clocks[program] = clock

        # A program switch is logged, but never resets the incoming
        # program's clock onto the outgoing one's.
        if prev_program is not None and program != prev_program:
            discontinuities.append({
                "index": index,
                "kind": "program_switch",
                "detail": f"{prev_program}->{program}",
            })
        prev_program = program

        rec = {"pts64": None, "pcr64": None, "ms": None}

        raw_pcr = pkt.get("pcr")
        if raw_pcr is not None:
            pcr64, kind = clock.unwrap_pcr(raw_pcr)
            rec["pcr64"] = pcr64
            if kind == "wrap":
                discontinuities.append(
                    {"index": index, "kind": "wrap", "detail": "pcr wrap"})
            elif kind == "reset":
                discontinuities.append(
                    {"index": index, "kind": "reset", "detail": "pcr reset"})

        raw_pts = pkt.get("pts")
        if raw_pts is not None:
            # Missing source timestamps stay missing: no interpolation.
            rec["pts64"] = clock.unwrap_pts(raw_pts)

        base = rec["pcr64"] if rec["pcr64"] is not None else rec["pts64"]
        if base is not None:
            rec["ms"] = round(base / TICKS_PER_MS, 3)

        out.append(rec)

    programs = {
        name: {"epoch": clock.epoch, "last_pcr": clock.last_raw_pcr}
        for name, clock in clocks.items()
    }
    return {
        "packets": out,
        "programs": programs,
        "discontinuities": discontinuities,
    }


def main():
    with open(sys.argv[1]) as f:
        data = json.load(f)
    result = recover(data["packets"])
    with open(sys.argv[2], "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
