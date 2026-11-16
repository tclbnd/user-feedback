#!/usr/bin/env python3
"""Regression comparison: the pre-fix implementation (from git HEAD) vs the
fixed one, run on the same remux fixture.  Demonstrates the LFE/Ls swap and
the alphabetical guessing of unknown layouts in the old code.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from remap import remap  # noqa: E402


def git_show_old():
    r = subprocess.run(["git", "show", "HEAD:remap.py"], cwd=HERE,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def main():
    with open(os.path.join(HERE, "fixtures", "remux51.json")) as f:
        fixture = json.load(f)

    with tempfile.TemporaryDirectory() as td:
        old_py = os.path.join(td, "old.py")
        old_in = os.path.join(td, "in.json")
        old_out = os.path.join(td, "old.json")
        with open(old_py, "w") as f:
            f.write(git_show_old())
        with open(old_in, "w") as f:
            json.dump(fixture, f)
        r = subprocess.run([sys.executable, old_py, old_in, old_out],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        with open(old_out) as f:
            old = json.load(f)

    new = remap(fixture)
    lfe = 3  # target position in L,R,C,LFE,Ls,Rs

    # --- Defect 1: missing channel.  With 5 source slots for 6 target
    # speakers the old alphabetical rank alignment silently shifts every
    # label whose rank exceeds the missing one's.
    print("partial segment seg2 (LFE absent; labels L,R,C,Ls,Rs):")
    print("  old code  LFE target slot[16] =", old["channels"][lfe][16],
          "(Ls pulse 203 misrouted into LFE!)")
    print("  new code  LFE target slot[16] =", new["channels"][lfe][16],
          "status =", new["segment_report"][2]["status"],
          "missing =", new["segment_report"][2]["missing"])
    assert old["channels"][lfe][16] == 203, "old code misroutes Ls into LFE"
    assert new["channels"][lfe][16] == 0
    assert new["segment_report"][2]["missing"] == ["LFE"]

    # --- Defect 2: unknown layout.  Sorted-rank guessing assigns foreign
    # Aux pulses to L/R/... instead of flagging the layout as unknown.
    print("\nunknown-layout segment seg3 (labels Aux1..Aux4):")
    print("  old code  L[24] =", old["channels"][0][24],
          "(foreign Aux2 pulse guessed by alphabet rank, no warning)")
    print("  new code  L[24] =", new["channels"][0][24],
          " status =", new["segment_report"][3]["status"])
    # Aux ranks occupy target ranks 0..3 by alphabetical alignment, so the
    # old code guesses Aux2's pulse (301) onto the L target position.
    assert old["channels"][0][24] == 301, "old code guesses by sorted order"
    assert new["channels"][0][24] == 0
    assert new["segment_report"][3]["status"] == "unknown"

    # --- Defect 3: duplicate labels are invisible to the old mapping.
    print("\nduplicate-label segment seg4 (Ls appears twice):")
    print("  old report =", old["segment_report"][4])
    print("  new report =", {k: new["segment_report"][4][k]
                             for k in ("status", "duplicates", "map")})
    assert old["segment_report"][4]["status"] == "ok"
    assert new["segment_report"][4]["status"] == "conflict"

    # --- Defect 4 (the reported ticket): same 5.1 source, remuxed into a
    # container whose track list carries an extra foreign slot.  Label
    # identity still resolves every 5.1 speaker, but the old sorted-rank
    # alignment shifts every output once the extra label sorts in early.
    trap = {
        "target_speakers": ["L", "R", "C", "LFE", "Ls", "Rs"],
        "segments": [{
            "start": 0, "end": 2,
            # 6 remuxed slots + one container-only extra track; pulses make
            # the physical source channel of every output traceable.
            "source_speakers": ["Aux0", "L", "R", "C", "LFE", "Ls", "Rs"],
            "samples": [[10], [11], [12], [13], [14], [15], [16]],
        }],
    }
    with tempfile.TemporaryDirectory() as td:
        old_py = os.path.join(td, "old.py")
        old_in = os.path.join(td, "in.json")
        old_out = os.path.join(td, "old.json")
        with open(old_py, "w") as f:
            f.write(git_show_old())
        with open(old_in, "w") as f:
            json.dump(trap, f)
        r = subprocess.run([sys.executable, old_py, old_in, old_out],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        with open(old_out) as f:
            old_trap = json.load(f)
    new_trap = remap(trap)
    print("\nremux trap (slots Aux0,L,R,C,LFE,Ls,Rs against a 5.1 target):")
    print("  old code  outputs =", [c[0] for c in old_trap["channels"]])
    print("  new code  outputs =", [c[0] for c in new_trap["channels"]])
    # Old sorted-rank alignment: every target speaker lands on the wrong
    # physical channel; the C output even receives the container-only Aux0
    # track, and the LFE output receives the L-labelled slot.
    assert [c[0] for c in old_trap["channels"]] == [13, 15, 10, 11, 14, 12]
    # New: Aux0 ignored, each target follows its own label.
    assert [c[0] for c in new_trap["channels"]] == [11, 12, 13, 14, 15, 16]
    assert new_trap["segment_report"][0]["map"] == [1, 2, 3, 4, 5, 6]

    print("\nREGRESSION CONFIRMED: HEAD guesses positions by sorted labels")
    print("(misrouting on partial/unknown/remuxed layouts, blind to")
    print("duplicates); the fixed remap follows explicit speaker ids.")


if __name__ == "__main__":
    main()
