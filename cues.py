#!/usr/bin/env python3
"""Resolve offline programme cue markers into effective programme-time cuts.

Input format and cue semantics are documented in README.md.  All state is kept
per programme and, where versioning or cancelling applies, per component: cue
ids are only unique inside one programme, and each component has its own local
time base.
"""

import json
import sys

_CUE_TYPES = {"immediate", "at", "per-component", "cancel"}


def _version(cue):
    return cue.get("version") or 0


def _components(program):
    """Return [(component_id, time_base), ...] in input order (deduplicated)."""
    result = []
    seen = set()
    for component in program.get("components", []):
        cid = component.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        result.append((cid, component.get("time_base") or 0))
    return result


def _at(cue):
    return cue.get("at") or 0


def _sort_text(value):
    """Normalise an optional id so mixed/absent values still sort sanely."""
    return "" if value is None else (value if isinstance(value, str) else str(value))


def _winning_cancels(cues):
    """Indices of cancel cues that survive versioning.

    Cancels are versioned like every other cue: for one (id, component) the
    highest version wins, and an exact tie keeps the first occurrence.  The
    filter happens up front so a losing cancel cannot leave a half-applied
    effect behind and cannot fight with the cut that replaces it.
    """
    best = {}
    for index, cue in enumerate(cues):
        if cue.get("type") != "cancel":
            continue
        key = (cue.get("id"), cue.get("component"))
        current = best.get(key)
        if current is None or _version(cue) > _version(cues[current]):
            best[key] = index
    return set(best.values())


def _resolve_program(program):
    """Resolve one programme's cues in input order.

    Every slot is keyed by (cue id, component), so a marker reused by another
    programme never collides with this one, and each cut a per-component cue
    emits is versioned on its own component.  Within a slot the higher version
    revises the standing cut and the first occurrence wins a tie; a cancel
    ends the standing cut, so a later, higher version is the restore.
    """
    components = _components(program)
    component_ids = [component_id for component_id, _ in components]
    time_bases = dict(components)
    cues = list(program.get("cues", []))

    slots = {}   # (cue_id, component_id) -> slot dict
    cuts = []    # every emitted cut, in programme order

    def emit(cue, component_id, at, state, version):
        cut = {
            "cue": cue.get("id"),
            "component": component_id,
            "at": at,
            "state": state,
            "version": version,
        }
        cuts.append(cut)
        return cut

    def place(cue, component_id, at, all_components):
        """Add one cut for `cue` on `component_id`.

        `all_components` is true for cuts emitted by a per-component cue: such
        a cut is bound to every component, which matters when a cancel is
        scoped to one component.
        """
        key = (cue.get("id"), component_id)
        version = _version(cue)
        slot = slots.get(key)

        if slot is not None and version <= slot["version"]:
            # The higher version wins and an exact tie keeps the first
            # occurrence, so this cut can never take the slot.  It is still
            # exported, marked superseded, and it cannot revive a slot whose
            # cut a cancel already ended.
            emit(cue, component_id, at, "superseded", version)
            return

        if slot is not None and slot["live"]:
            # The revision replaces the cut standing in the slot; that cut
            # keeps its own (lower) version in the export.
            slot["cut"]["state"] = "superseded"

        # When the slot is not live its previous cut stays "cancelled" and
        # this higher version is the restore that follows the cancel.
        slots[key] = {
            "version": version,
            "cut": emit(cue, component_id, at, "active", version),
            "live": True,
            "all_components": all_components,
        }

    def register(cue):
        if cue.get("type") == "per-component":
            # `at` is component-local for this type: add each component's
            # time base to reach programme time and emit one cut per
            # component, each in that component's own version slot.
            for component_id in component_ids:
                place(cue, component_id, _at(cue) + time_bases[component_id], True)
        else:
            # "immediate" and "at" already carry programme time.
            place(cue, cue.get("component"), _at(cue), False)

    def cancel(cue):
        target = cue.get("target")
        scope = cue.get("component")
        # A cancel on component X removes the target's cut when that cut is
        # bound to X or to all components (each cut of a per-component cue).  A
        # cut bound only to another component is out of scope, as is every
        # other programme.  A cancel without a component applies programme-wide.
        scoped = scope is not None
        for (cue_id, component_id), slot in slots.items():
            if cue_id != target:
                continue
            if scoped and not slot["all_components"] and component_id != scope:
                continue
            if not slot["live"]:
                # Already cancelled; a second cancel changes nothing.
                continue
            slot["cut"]["state"] = "cancelled"
            slot["live"] = False

    winning = _winning_cancels(cues)
    for index, cue in enumerate(cues):
        cue_type = cue.get("type")
        if cue_type == "cancel":
            if index in winning:
                cancel(cue)
        elif cue_type in _CUE_TYPES:
            register(cue)

    cuts.sort(key=lambda cut: (_at(cut), _sort_text(cut["component"]),
                               _sort_text(cut["cue"])))
    return {"cuts": cuts}


def resolve(document):
    programs = {}
    for program in document.get("programs", []):
        programs[program.get("id")] = _resolve_program(program)

    resume = []
    for request in document.get("resume", []):
        program_id = request.get("program")
        start = request.get("from") or 0
        cuts = programs.get(program_id, {}).get("cuts", [])
        ids = sorted(
            (cut["cue"] for cut in cuts
             if cut["state"] == "active" and _at(cut) >= start),
            key=_sort_text,
        )
        resume.append({"program": program_id, "from": start, "cuts": ids})

    return {"programs": programs, "resume": resume}


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        raise SystemExit("usage: python3 cues.py input.json output.json")

    with open(argv[1], "r", encoding="utf-8") as source:
        document = json.load(source)

    result = resolve(document)

    with open(argv[2], "w", encoding="utf-8") as target:
        json.dump(result, target, indent=1)
        target.write("\n")


if __name__ == "__main__":
    main()
