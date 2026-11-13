"""Mixed telecine / native-interlaced field-sequence replay.

Input is ``{"fields": [{"id", "parity", "rows", "cut"}, ...]}``.  ``top``
fields carry the original even scanlines, ``bottom`` fields the original odd
scanlines.  One field lasts 1001/60000 s and ``cut`` marks a hard cut *before*
that field.

Telecine groups are found from adjacent-field weave consistency only; no
fixed five-field cycle is trusted across a cut, because that would weave
fields from two different shots.  Native interlaced fields, undecidable
fields and truncated fields are kept in arrival order instead.

All timing is exact rational arithmetic.  The output clock is anchored to the
integer field instant at the start of every cut-delimited shot and advances by
the exact output-frame duration, so accumulated drift at a cut is bounded and
reported.
"""

import argparse
import hashlib
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path


FIELD = Fraction(1001, 60000)           # one input field
PROG = Fraction(1001, 24000)            # one reconstructed progressive frame
FPS_PROGRESSIVE = [24000, 1001]
FPS_INTERLACED_PAIR = [30000, 1001]
FPS_INTERLACED_SINGLE = [60000, 1001]

# A two-field weave is credible when its adjacent-row discontinuity is no
# larger than this multiple of the ordinary within-field activity.
COMPATIBLE_RATIO = 4.0
# Small prior favouring a 2,3,2,3... cadence.  It only decides between
# groupings that are equally well supported by the picture (e.g. static
# duplicate source frames); strong evidence always wins.
CADENCE_PENALTY = 0.35
# Cost of leaving one field native/undecided.  Deliberately large enough that
# any credible telecine pair is preferred.
INTERLACED_COST = 0.55


def rat(value):
    f = Fraction(value)
    return [f.numerator, f.denominator]


def rows_of(field):
    out = []
    for row in field.get("rows") or []:
        try:
            out.append([int(v) for v in row])
        except (TypeError, ValueError):
            out.append([0 for _ in row])
    return out


def sha_rows(rows):
    return hashlib.sha256(bytes(v & 0xFF for row in rows for v in row)).hexdigest()


def row_activity(rows):
    """Mean absolute difference between vertically adjacent scanlines."""
    if len(rows) < 2:
        return 0.0
    total = 0
    count = 0
    for a, b in zip(rows, rows[1:]):
        for x, y in zip(a, b):
            total += abs(x - y)
            count += 1
    return total / count if count else 0.0


def weave(top_rows, bottom_rows):
    """Interleave top and bottom rows, starting with top row 0."""
    out = []
    for k in range(max(len(top_rows), len(bottom_rows))):
        if k < len(top_rows):
            out.append(top_rows[k])
        if k < len(bottom_rows):
            out.append(bottom_rows[k])
    return out


def opposite(parity):
    return "bottom" if parity == "top" else "top"


def field_id(fields, i):
    return fields[i].get("id", i)


class Analyser:
    """Local, cut-aware analysis of one field stream."""

    def __init__(self, fields):
        self.fields = fields
        self.rows = [rows_of(f) for f in fields]
        self.diagnostics = []

        heights = [len(r) for r in self.rows if r]
        widths = [len(row) for r in self.rows for row in r if row]
        height_counts = Counter(heights)
        self.mode_height = height_counts.most_common(1)[0][0] if heights else 0
        self.mode_width = Counter(widths).most_common(1)[0][0] if widths else 0
        # Half-height fields of an odd-height source picture have two legal
        # heights (ceil and floor of full_height / 2).  Accept a shorter
        # height only when it is a recurring structural fact of the stream
        # (several fields and both field parities present); an isolated short
        # field is a truncated field.
        parities = {f.get("parity") for f in self.fields}
        self.allowed_heights = set(height_counts)
        shorter = self.mode_height - 1
        if (len(fields) < 4 or "top" not in parities or "bottom" not in parities
                or height_counts.get(shorter, 0) < 2):
            self.allowed_heights = {self.mode_height}

        self.structural = [self._check_structure(i) for i in range(len(fields))]
        self._activity = [row_activity(r) for r in self.rows]
        positive = [a for a in self._activity if a > 0]
        self.scale = sorted(positive)[len(positive) // 2] if positive else 1.0
        self.scale = max(self.scale, 1e-6)

    # -- input sanity ------------------------------------------------------

    def _check_structure(self, i):
        rows = self.rows[i]
        fid = field_id(self.fields, i)
        problems = []
        if not rows:
            problems.append("empty")
        else:
            if any(len(r) != self.mode_width for r in rows):
                problems.append("ragged_width")
            if self.mode_height and len(rows) not in self.allowed_heights:
                problems.append("truncated_height")
        if any(not (0 <= v <= 255) for r in rows for v in r):
            problems.append("out_of_range")

        if {"empty", "ragged_width", "truncated_height"} & set(problems):
            self.diagnostics.append({
                "code": "truncated_field",
                "field_id": fid,
                "field_index": i,
                "declared_parity": self.fields[i].get("parity"),
                "expected_rows": self.mode_height,
                "actual_rows": len(rows),
                "expected_width": self.mode_width,
                "actual_widths": sorted({len(r) for r in rows}),
                "field_sha256": sha_rows(rows),
                "problems": problems,
                "action": "kept as an interlaced field; excluded from weaving",
            })
        if "out_of_range" in problems:
            self.diagnostics.append({
                "code": "value_out_of_range",
                "field_id": fid,
                "field_index": i,
                "field_sha256": sha_rows(rows),
                "action": "values masked to 8 bits before hashing",
            })
        return not problems

    # -- local measurements ------------------------------------------------

    def activity(self, i):
        return self._activity[i]

    def weave_diff(self, top_i, bottom_i):
        return row_activity(weave(self.rows[top_i], self.rows[bottom_i]))

    def pair_ratio(self, i, pi, j, pj):
        """Discontinuity of a two-field weave divided by local activity.

        A small ratio means the two fields are the two halves of one source
        frame; a large ratio means the pictures disagree (motion or a cut).
        """
        if not self.structural[i] or not self.structural[j]:
            return float("inf")
        if pi == pj:
            return float("inf")
        top, bottom = (i, j) if pi == "top" else (j, i)
        comb = self.weave_diff(top, bottom)
        motion = (self.activity(i) + self.activity(j)) / 2.0
        return comb / max(motion, self.scale * 0.25, 1e-9)

    def content_diff(self, i, j):
        """Mean absolute picture difference, independent of field order."""
        a = [v for r in self.rows[i] for v in r]
        b = [v for r in self.rows[j] for v in r]
        if not a or len(a) != len(b):
            return float("inf")
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    # -- field order -------------------------------------------------------

    def infer_parities(self, starts):
        """Local field order, repairing invalid or self-contradicting labels.

        Field order may legitimately switch at a cut, so no comparison is
        made across a shot boundary.  Returns ``(effective, source)`` where
        ``source[i]`` says whether field ``i``'s order was declared, invented
        from the alternating pattern, or repaired from the weave.
        """
        n = len(self.fields)
        boundaries = set(starts)
        eff = []
        source = []
        for i, f in enumerate(self.fields):
            p = f.get("parity")
            eff.append(p if p in ("top", "bottom") else None)
            source.append("declared")

        for i in range(n):
            fid = field_id(self.fields, i)
            if eff[i] is None:
                if i > 0 and i not in boundaries:
                    eff[i] = opposite(eff[i - 1])
                else:
                    eff[i] = "top" if i % 2 == 0 else "bottom"
                source[i] = "inferred"
                self.diagnostics.append({
                    "code": "invalid_parity",
                    "field_id": fid,
                    "field_index": i,
                    "declared": self.fields[i].get("parity"),
                    "inferred": eff[i],
                })
                continue
            if i == 0 or i in boundaries:
                continue
            if eff[i] == eff[i - 1]:
                alt = opposite(eff[i])
                ratio = self.pair_ratio(i - 1, eff[i - 1], i, alt)
                if ratio <= COMPATIBLE_RATIO:
                    self.diagnostics.append({
                        "code": "parity_mismatch",
                        "field_id": fid,
                        "field_index": i,
                        "declared": self.fields[i].get("parity"),
                        "inferred": alt,
                        "evidence_weave_ratio": round(ratio, 6),
                        "note": "repeated parity contradicted by a credible two-field weave",
                    })
                    eff[i] = alt
                    source[i] = "repaired"
                else:
                    # The picture itself breaks here.  Treat it as a local
                    # field-order switch (or a cut): keep the declared order
                    # and let grouping reset, rather than forcing a weave
                    # across incompatible content.
                    source[i] = "declared_break"
        return eff, source

    # -- cut inference -----------------------------------------------------

    def infer_cuts(self, declared):
        """Detect unmarked hard cuts without trusting a single motion metric.

        An inferred cut requires several independent signals:
          * both fields are structurally usable;
          * the adjacent fields repeat a parity (a shot may restart there);
          * no weave across the boundary is credible;
          * the picture discontinuity is an outlier of local activity.

        The rule is deliberately conservative: ordinary 2:3 group boundaries
        must never be mistaken for cuts.
        """
        declared_set = set(declared)
        diffs = [None] * len(self.fields)
        for i in range(1, len(self.fields)):
            if self.structural[i] and self.structural[i - 1]:
                diffs[i] = self.content_diff(i - 1, i)
        valid = [d for d in diffs if d is not None and d != float("inf")]
        if not valid:
            return []
        median_diff = sorted(valid)[len(valid) // 2]
        out = []
        for i in range(1, len(self.fields)):
            if i in declared_set or diffs[i] is None or diffs[i] == float("inf"):
                continue
            f_prev, f_cur = self.fields[i - 1], self.fields[i]
            p_prev = f_prev.get("parity")
            p_cur = f_cur.get("parity")
            ratio = self.pair_ratio(i - 1, p_prev, i, p_cur)
            repeated = p_prev == p_cur
            big_change = diffs[i] >= max(8.0 * median_diff + 1.0, 6.0 * self.scale)
            if repeated and ratio > COMPATIBLE_RATIO and big_change:
                out.append(i)
                self.diagnostics.append({
                    "code": "missing_cut_inferred",
                    "field_id": field_id(self.fields, i),
                    "field_index": i,
                    "previous_field_id": field_id(self.fields, i - 1),
                    "evidence_content_diff": round(diffs[i], 6),
                    "evidence_weave_ratio": None if ratio == float("inf") else round(ratio, 6),
                    "action": "matching and cadence phase reset at this boundary",
                })
        return out

    # -- cadence / grouping ------------------------------------------------

    def analyse_shot(self, s, e, eff):
        """Choose progressive 2/3-field groups and native fields for a shot.

        Dynamic programme over the shot's fields.  A group may never cross the
        shot boundary, so a hard cut always resets cross-field matching and the
        cadence phase.
        """
        n = e - s
        ratios = {}
        for i in range(s, e - 1):
            ratios[i] = self.pair_ratio(i, eff[i], i + 1, eff[i + 1])

        def group3_ok(i):
            if i + 3 > e:
                return False
            if ratios.get(i, 99.0) > COMPATIBLE_RATIO:
                return False
            if ratios.get(i + 1, 99.0) > COMPATIBLE_RATIO:
                return False
            if eff[i] == eff[i + 1] or eff[i] != eff[i + 2]:
                return False
            # In a telecine triple the first and third fields are the very
            # same field.  Requiring exact identity prevents stretching a
            # group across a source-frame boundary or across near-static
            # frames that merely look alike.
            return rows_of(self.fields[i]) == rows_of(self.fields[i + 2])

        def pair_ok(i):
            return (i + 2 <= e and ratios.get(i, 99.0) <= COMPATIBLE_RATIO
                    and eff[i] != eff[i + 1])

        dp = [dict() for _ in range(n + 1)]
        dp[0]["start"] = (0.0, None, None, None)

        def relax(k, state, cost, prev_k, prev_state, action):
            old = dp[k].get(state)
            if old is None or cost < old[0] - 1e-12:
                dp[k][state] = (cost, prev_k, prev_state, action)

        for k in range(n):
            if not dp[k]:
                continue
            for state, (cost, prev_k, prev_state, action) in list(dp[k].items()):
                i = s + k
                single = 0.0 if not self.structural[i] else INTERLACED_COST
                relax(k + 1, "I", cost + single, k, state, ("interlaced", i, 1))
                if pair_ok(i):
                    extra = CADENCE_PENALTY if state == 2 else 0.0
                    relax(k + 2, 2, cost + 0.05 + extra, k, state,
                          ("progressive", i, 2))
                if group3_ok(i):
                    extra = CADENCE_PENALTY if state == 3 else 0.0
                    relax(k + 3, 3, cost + 0.05 + extra, k, state,
                          ("progressive", i, 3))

        best_state = min(dp[n], key=lambda st: dp[n][st][0])
        actions = []
        k, state = n, best_state
        while k > 0:
            _, prev_k, prev_state, action = dp[k][state]
            actions.append(action)
            k, state = prev_k, prev_state
        actions.reverse()
        return actions


def convert(data):
    fields = data.get("fields") or []
    analyser = Analyser(fields)
    n = len(fields)

    if n == 0:
        analyser.diagnostics.append({
            "code": "empty_input",
            "action": "nothing to replay",
        })
        return {
            "frames": [],
            "segments": [],
            "provenance": [],
            "diagnostics": analyser.diagnostics,
            "timeline": _timeline([], [], analyser, Fraction(0)),
        }

    # A hard cut starts a new shot: matching, field order and cadence phase
    # are all reset there.  Declared cuts are authoritative; unmarked cuts are
    # inferred conservatively from picture discontinuities below.
    declared = [0] + [i for i, f in enumerate(fields) if i > 0 and f.get("cut")]
    declared = sorted(set(i for i in declared if 0 <= i < n))
    inferred_cuts = analyser.infer_cuts(declared)
    starts = sorted(set(declared + inferred_cuts))
    eff, parity_source = analyser.infer_parities(starts)
    ends = starts[1:] + [n]
    analyser.parity_source = parity_source

    frames = []
    shots = []
    for s, e in zip(starts, ends):
        if s >= e:
            continue
        actions = analyser.analyse_shot(s, e, eff)
        if e - s <= 2:
            analyser.diagnostics.append({
                "code": "short_shot",
                "field_ids": [field_id(fields, i) for i in range(s, e)],
                "field_count": e - s,
                "action": "matched only where a credible two-field weave exists",
            })

        # Exact rational clock, anchored to the integer field instant at the
        # shot start.
        t = Fraction(s) * FIELD
        shot_start = t
        shot_frames = []

        pending = []

        def flush_interlaced():
            nonlocal pending, t
            pos = 0
            while pos < len(pending):
                group = pending[pos:pos + 2]
                pos += len(group)
                ids = [field_id(fields, j) for j in group]
                present = []
                for j in group:
                    present.extend(analyser.rows[j])
                duration = Fraction(len(group)) * FIELD
                frame = {
                    "mode": "interlaced",
                    "field_ids": ids,
                    "field_order": [eff[j] for j in group],
                    "sha256": sha_rows(present),
                    "pts": rat(t),
                    "duration": rat(duration),
                    "field_count": len(group),
                    "sources": [{
                        "field_id": field_id(fields, j),
                        "field_index": j,
                        "parity": eff[j],
                        "role": "leftover" if len(group) == 1 else "native_field",
                    } for j in group],
                }
                frames.append(frame)
                shot_frames.append(frame)
                t += duration
                if len(group) == 1:
                    analyser.diagnostics.append({
                        "code": "leftover_field",
                        "field_id": ids[0],
                        "field_index": group[0],
                        "duration": rat(FIELD),
                        "duration_seconds": float(FIELD),
                        "note": "single remaining field kept in arrival order",
                    })
            pending = []

        for kind, i, length in actions:
            if kind == "progressive":
                flush_interlaced()
                group = list(range(i, i + length))
                tops = [j for j in group if eff[j] == "top"]
                bottoms = [j for j in group if eff[j] == "bottom"]
                usable = (
                    all(analyser.structural[j] for j in group)
                    and len(tops) >= 1 and len(bottoms) >= 1
                    and all(rows_of(fields[j]) == rows_of(fields[tops[0]])
                            for j in tops)
                    and all(rows_of(fields[j]) == rows_of(fields[bottoms[0]])
                            for j in bottoms)
                )
                if not usable:
                    analyser.diagnostics.append({
                        "code": "group_rejected",
                        "field_ids": [field_id(fields, j) for j in group],
                        "reason": "group is not a usable top/bottom pair",
                    })
                    pending.extend(group)
                    continue

                woven = weave(analyser.rows[tops[0]], analyser.rows[bottoms[0]])
                sources = []
                dropped = []
                for j in group:
                    role = "duplicate" if j not in (tops[0], bottoms[0]) else (
                        "weave_top" if eff[j] == "top" else "weave_bottom")
                    if role == "duplicate":
                        dropped.append(field_id(fields, j))
                    sources.append({
                        "field_id": field_id(fields, j),
                        "field_index": j,
                        "parity": eff[j],
                        "role": role,
                    })
                frame = {
                    "mode": "progressive",
                    "field_ids": [field_id(fields, j) for j in group],
                    "field_order": "top_first" if eff[group[0]] == "top" else "bottom_first",
                    "sha256": sha_rows(woven),
                    "pts": rat(t),
                    "duration": rat(PROG),
                    "field_count": length,
                    "sources": sources,
                    "dropped_duplicate_field_ids": dropped,
                }
                frames.append(frame)
                shot_frames.append(frame)
                t += PROG
            else:
                pending.append(i)
        flush_interlaced()

        # Rational timing audit against the integer field instants.
        drift = (t - Fraction(e) * FIELD) / FIELD
        shots.append({
            "start_field_index": s,
            "end_field_index": e,
            "field_count": e - s,
            "frame_count": len(shot_frames),
            "pts_start": rat(shot_start),
            "pts_end": rat(t),
            "drift_fields": rat(drift),
            "cadence": _cadence(actions),
            "field_order": _shot_field_order(actions, eff),
        })
        if abs(drift) > 1:
            analyser.diagnostics.append({
                "code": "timing_drift_exceeded",
                "start_field_index": s,
                "end_field_index": e,
                "field_ids": [field_id(fields, i) for i in range(s, e)],
                "drift_fields": rat(drift),
                "limit_fields": 1,
                "action": "grouping kept for picture fidelity; timing flag raised",
            })

    segments = build_segments(frames)
    _report_interlaced_runs(frames, analyser, fields)
    _check_cut_flags(fields, analyser, eff)

    # Explicit original-field -> result-frame provenance.
    provenance = []
    for frame_index, frame in enumerate(frames):
        for source in frame.get("sources", []):
            provenance.append({
                "field_id": source["field_id"],
                "field_index": source["field_index"],
                "frame_index": frame_index,
                "mode": frame["mode"],
                "role": source["role"],
            })
    covered = {p["field_index"] for p in provenance}
    if len(covered) != n:
        analyser.diagnostics.append({
            "code": "unaccounted_fields",
            "field_indices": sorted(set(range(n)) - covered),
        })

    max_drift = Fraction(0)
    for shot in shots:
        max_drift = max(max_drift, abs(Fraction(*shot["drift_fields"])))
    if shots:
        analyser.diagnostics.append({
            "code": "timing_alignment",
            "max_abs_drift_fields": rat(max_drift),
            "drift_limit_fields": 1,
            "clock": "exact rational field instants, re-anchored at every hard cut",
        })

    return {
        "frames": frames,
        "segments": segments,
        "provenance": provenance,
        "diagnostics": analyser.diagnostics,
        "timeline": _timeline(frames, shots, analyser, max_drift),
    }


def _cadence(actions):
    sizes = [a[2] for a in actions if a[0] == "progressive"]
    info = {"group_sizes": sizes}
    if sizes and set(sizes) <= {2, 3}:
        info["period_fields"] = 5
        info["pattern"] = "2:3" if sizes[0] == 2 else "3:2"
        alternating = all(a != b for a, b in zip(sizes, sizes[1:]))
        info["alternating"] = alternating
    return info


def _shot_field_order(actions, eff):
    for kind, i, length in actions:
        if kind == "progressive":
            return "top_first" if eff[i] == "top" else "bottom_first"
    return None


def _timeline(frames, shots, analyser, max_drift=Fraction(0)):
    total = sum((Fraction(*f["duration"]) for f in frames), Fraction(0))
    return {
        "field_duration": rat(FIELD),
        "field_rate": [60000, 1001],
        "progressive_duration": rat(PROG),
        "progressive_fps": FPS_PROGRESSIVE,
        "interlaced_pair_fps": FPS_INTERLACED_PAIR,
        "single_field_fps": FPS_INTERLACED_SINGLE,
        "single_field_duration": rat(FIELD),
        "total_duration": rat(total),
        "max_abs_drift_fields": rat(max_drift),
        "drift_limit_fields": 1,
        "cut_field_indices": [
            i for i, f in enumerate(analyser.fields) if i > 0 and f.get("cut")
        ],
        "shots": shots,
    }


def _report_interlaced_runs(frames, analyser, fields):
    """Record every contiguous native/undecidable run and why it was kept."""
    i = 0
    while i < len(frames):
        if frames[i]["mode"] != "interlaced":
            i += 1
            continue
        j = i
        ids = []
        while j < len(frames) and frames[j]["mode"] == "interlaced":
            ids.extend(frames[j]["field_ids"])
            j += 1
        analyser.diagnostics.append({
            "code": "interlaced_preserved",
            "field_ids": ids,
            "field_count": len(ids),
            "reason": "no credible adjacent-field weave; original order kept",
        })
        i = j


def _check_cut_flags(fields, analyser, eff):
    """Flag cut markers that do not coincide with a picture discontinuity."""
    for i, f in enumerate(fields):
        if i == 0 or not f.get("cut"):
            continue
        if not analyser.structural[i] or not analyser.structural[i - 1]:
            continue
        ratio = analyser.pair_ratio(i - 1, eff[i - 1], i, eff[i])
        if ratio <= COMPATIBLE_RATIO:
            analyser.diagnostics.append({
                "code": "cut_without_discontinuity",
                "field_id": field_id(fields, i),
                "field_index": i,
                "previous_field_id": field_id(fields, i - 1),
                "evidence_weave_ratio": round(ratio, 6),
                "action": "cut honoured (no weave across it); marker looks spurious",
            })


def build_segments(frames):
    segments = []
    current = None
    for index, frame in enumerate(frames):
        if frame["mode"] == "progressive":
            fps = FPS_PROGRESSIVE
        else:
            fps = FPS_INTERLACED_PAIR if frame["field_count"] == 2 else FPS_INTERLACED_SINGLE
        key = (frame["mode"], tuple(fps))
        if current is None or current["_key"] != key:
            if current is not None:
                segments.append(current)
            current = {
                "_key": key,
                "mode": frame["mode"],
                "fps": list(fps),
                "start_frame": index,
                "end_frame": index,
                "frame_count": 0,
                "field_count": 0,
                "field_ids": [],
                "duration": Fraction(0),
                "pts_start": Fraction(*frame["pts"]),
            }
        current["end_frame"] = index
        current["frame_count"] += 1
        current["field_count"] += frame["field_count"]
        current["field_ids"].extend(frame["field_ids"])
        current["duration"] += Fraction(*frame["duration"])
    if current is not None:
        segments.append(current)

    out = []
    for seg in segments:
        seg.pop("_key", None)
        seg["duration"] = rat(seg["duration"])
        seg["pts_start"] = rat(seg["pts_start"])
        seg["pts_end"] = rat(Fraction(*seg["pts_start"]) + Fraction(*seg["duration"]))
        if seg["mode"] == "interlaced" and seg["field_count"] == 1:
            seg["single_field"] = True
            seg["single_field_duration"] = rat(FIELD)
            seg["field_ids"] = seg["field_ids"][:1]
        out.append(seg)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    result = convert(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
