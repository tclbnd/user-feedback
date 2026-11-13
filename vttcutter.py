# -*- coding: utf-8 -*-
"""EDL driven WebVTT cutter core.

All time arithmetic uses exact rational seconds (fractions.Fraction).  Cues
from every source are intersected with every clip referencing that source, so
a cue may appear several times in the output and may span a hard cut.
Millisecond rounding happens once, at the end; half a millisecond rounds up,
and quantised zero-length cues are dropped.
"""
import json
import re
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

FRAME_TICK = Fraction(1001, 30000)          # seconds per source frame
SPEED_LO = Fraction(1, 4)
SPEED_HI = Fraction(4, 1)

# hour form must be tried first so 00:00:00.333 is not read as mm:ss
_TS_HOUR = re.compile(r"^(\d+):(\d{1,2}):(\d{2})\.(\d{3})$")
_TS_MINUTE = re.compile(r"^(\d{1,2}):(\d{2})\.(\d{3})$")
CUE_TIMING = re.compile(
    r"((?:\d+:)?\d{1,2}:\d{2}\.\d{3})\s*-->\s*"
    r"((?:\d+:)?\d{1,2}:\d{2}\.\d{3})([ \t].*)?$"
)
INLINE_TS = re.compile(r"<((?:\d+:)?\d{1,2}:\d{2}\.\d{3})>")
OPEN_TAG = re.compile(r"<([0-9A-Za-z]+)((?:\.[A-Za-z0-9_-]+)+)([^>]*)>")
CLASS_SELECTOR = re.compile(
    r"^(::cue)\(\.([A-Za-z0-9_-]+)\)([ \t]*\{.*\})[ \t]*$"
)
BOM = chr(0xFEFF)


class CutError(Exception):
    """Validation error carrying a precise source location."""


def parse_timestamp(text, where):
    """Parse a WebVTT timestamp into exact Fraction seconds."""
    mh = _TS_HOUR.match(text)
    if mh is not None:
        hour = int(mh.group(1))
        minute = int(mh.group(2))
        second = int(mh.group(3))
        milli = int(mh.group(4))
    else:
        mm = _TS_MINUTE.match(text)
        if mm is None:
            raise CutError(where + ": malformed timestamp " + repr(text))
        hour = 0
        minute = int(mm.group(1))
        second = int(mm.group(2))
        milli = int(mm.group(3))
    if minute >= 60 or second >= 60:
        raise CutError(where + ": timestamp field out of range in "
                       + repr(text))
    return (Fraction(hour * 3600 + minute * 60 + second)
            + Fraction(milli, 1000))


def to_ms(value):
    """Round Fraction seconds to whole milliseconds, halves up."""
    rounded = value * 1000 + Fraction(1, 2)
    return rounded.numerator // rounded.denominator


def fmt_millis(total_ms):
    total_ms = int(total_ms)
    sec, ms = divmod(total_ms, 1000)
    hour, sec = divmod(sec, 3600)
    minute, second = divmod(sec, 60)
    return "%02d:%02d:%02d.%03d" % (hour, minute, second, ms)


@dataclass
class Cue:
    source: str
    source_id: object            # explicit id string or None
    line: int                    # 1-based line of the timing row
    start: Fraction
    end: Fraction
    settings: str
    body_lines: list


@dataclass
class StyleBlock:
    source: str
    line: int                    # 1-based line of the STYLE keyword
    rules: list


@dataclass
class RegionBlock:
    source: str
    line: int
    lines: list


@dataclass
class SourceDoc:
    label: str
    path: Path
    cues: list = field(default_factory=list)
    styles: list = field(default_factory=list)
    regions: list = field(default_factory=list)
    region_ids: set = field(default_factory=set)
    class_names: set = field(default_factory=set)
    region_refs: dict = field(default_factory=dict)

def parse_vtt(path, label):
    doc = SourceDoc(label=label, path=path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CutError(label + " (" + str(path) + "): source file not found")
    except OSError as exc:
        raise CutError(label + " (" + str(path) + "): cannot read: "
                       + str(exc))
    if text.startswith(BOM):
        text = text[1:]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    lines = [ln.rstrip("\r") for ln in lines]

    if not lines or not lines[0].startswith("WEBVTT") or (
        len(lines[0]) > 6 and lines[0][6] not in (" ", "\t")
    ):
        raise CutError(label + " (" + str(path) +
                       "): missing WEBVTT header at line 1")

    # split into blocks on blank lines, tracking 1-based line numbers
    blocks = []
    start = 0
    for idx in range(len(lines) + 1):
        if idx == len(lines) or lines[idx].strip() == "":
            if idx > start:
                blocks.append((start + 1, lines[start:idx]))
            start = idx + 1

    for line_no, block in blocks:
        first = block[0].strip()
        if first == "WEBVTT" or first.startswith("WEBVTT "):
            continue
        if first == "NOTE":
            continue
        if first == "STYLE":
            doc.styles.append(StyleBlock(label, line_no, block[1:]))
            continue
        if first == "REGION":
            region = RegionBlock(label, line_no, block[1:])
            doc.regions.append(region)
            for payload in block[1:]:
                key, sep, value = payload.partition(":")
                if sep and key.strip() == "id":
                    doc.region_ids.add(value.strip())
            continue
        timing_index = None
        for j, row in enumerate(block):
            if CUE_TIMING.match(row.strip()):
                timing_index = j
                break
        if timing_index is None:
            raise CutError(label + " (" + str(path) +
                           "): unsupported text block at line "
                           + str(line_no))
        id_lines = [ln.strip() for ln in block[:timing_index]]
        if len(id_lines) > 1:
            raise CutError(label + " (" + str(path) +
                           "): malformed cue id at line " + str(line_no))
        timing_line = block[timing_index].strip()
        m = CUE_TIMING.match(timing_line)
        where = "%s (%s) line %d" % (label, path,
                                     line_no + timing_index)
        cue_start = parse_timestamp(m.group(1), where)
        cue_end = parse_timestamp(m.group(2), where)
        if cue_end <= cue_start:
            raise CutError(where + ": cue end must be greater than start")
        settings = m.group(3) or ""
        body = block[timing_index + 1:]
        if not body or not any(row.strip() for row in body):
            raise CutError(where + ": cue has empty text")
        cue = Cue(
            source=label,
            source_id=id_lines[0] if id_lines else None,
            line=line_no + timing_index,
            start=cue_start,
            end=cue_end,
            settings=settings,
            body_lines=list(body),
        )
        doc.cues.append(cue)
        for token in settings.split():
            key, _, value = token.partition(":")
            if key == "region":
                doc.region_refs.setdefault(cue.line, set()).add(value)
        last_marker = None
        for row in body:
            for mm in INLINE_TS.finditer(row):
                t = parse_timestamp(mm.group(1),
                                    where + ": inline timestamp")
                if last_marker is not None and t <= last_marker:
                    raise CutError(where + ": inline timestamps must be "
                                   "strictly increasing")
                last_marker = t
            for om in OPEN_TAG.finditer(row):
                for cls in om.group(2).lstrip(".").split("."):
                    doc.class_names.add(cls)

    prev = None
    for cue in doc.cues:
        if prev is not None and cue.start < prev.start:
            raise CutError("%s (%s) line %d: cues must be ordered by "
                           "non-decreasing start time"
                           % (label, path, cue.line))
        prev = cue
    return doc

@dataclass
class Clip:
    id: str
    source: str
    start_frame: int
    end_frame: int
    speed: Fraction
    index: int
    out_start: Fraction = Fraction(0)
    out_end: Fraction = Fraction(0)
    src_start: Fraction = Fraction(0)
    src_end: Fraction = Fraction(0)


_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def load_edl(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CutError("EDL " + str(path) + ": cannot read file: "
                       + str(exc))
    try:
        edl = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CutError("EDL %s line %d column %d: invalid JSON: %s"
                       % (path, exc.lineno, exc.colno, exc.msg))
    if not isinstance(edl, dict):
        raise CutError("EDL " + str(path) + ": top level must be an object")
    sources = edl.get("sources")
    clips_raw = edl.get("clips")
    if not isinstance(sources, dict) or not sources:
        raise CutError("EDL " + str(path) +
                       ": 'sources' must be a non-empty object")
    if not isinstance(clips_raw, list) or not clips_raw:
        raise CutError("EDL " + str(path) +
                       ": 'clips' must be a non-empty array")

    for label, rel in sources.items():
        if not isinstance(label, str) or not _LABEL_RE.match(label):
            raise CutError("EDL " + str(path) + ": unsafe source label "
                           + repr(label))
        if (not isinstance(rel, str) or not rel
                or Path(rel).is_absolute()
                or ".." in Path(rel).parts
                or rel.startswith(("/", "\\")) or "\\" in rel):
            raise CutError("EDL " + str(path) + ": unsafe source path "
                           + repr(rel) + " for " + label)

    seen_ids = set()
    clips = []
    for i, raw in enumerate(clips_raw):
        where = "EDL %s clips[%d]" % (path, i)
        if not isinstance(raw, dict):
            raise CutError(where + ": clip must be an object")
        cid = raw.get("id")
        if not isinstance(cid, str) or not cid:
            raise CutError(where + ": missing or invalid 'id'")
        if cid in seen_ids:
            raise CutError(where + ": duplicate clip id " + repr(cid))
        seen_ids.add(cid)
        source = raw.get("source")
        if source not in sources:
            raise CutError(where + ": references missing source "
                           + repr(source))
        sf = raw.get("start_frame")
        ef = raw.get("end_frame")
        if not isinstance(sf, int) or isinstance(sf, bool) or sf < 0:
            raise CutError(where +
                           ": start_frame must be a non-negative integer")
        if not isinstance(ef, int) or isinstance(ef, bool) or ef < 0:
            raise CutError(where +
                           ": end_frame must be a non-negative integer")
        if ef <= sf:
            raise CutError(where +
                           ": end_frame must be greater than start_frame")
        speed_raw = raw.get("speed", [1, 1])
        if not (isinstance(speed_raw, list) and len(speed_raw) == 2 and
                all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                    for x in speed_raw)):
            raise CutError(where +
                           ": speed must be [positive int, positive int]")
        speed = Fraction(speed_raw[0], speed_raw[1])
        if not (SPEED_LO <= speed <= SPEED_HI):
            raise CutError(where + ": speed " + repr(speed_raw)
                           + " outside [1/4, 4]")
        clips.append(Clip(cid, source, sf, ef, speed, i))

    cursor = Fraction(0)
    for clip in clips:
        clip.src_start = clip.start_frame * FRAME_TICK
        clip.src_end = clip.end_frame * FRAME_TICK
        clip.out_start = cursor
        clip.out_end = (cursor
                        + (clip.src_end - clip.src_start) / clip.speed)
        cursor = clip.out_end
    return sources, clips

def build_renames(source_docs):
    """Same class/region name in different sources gets <label>__<name>."""
    name_sources = {}
    for doc in source_docs.values():
        names = set(doc.class_names) | set(doc.region_ids)
        for block in doc.styles:
            for rule in block.rules:
                m = CLASS_SELECTOR.match(rule.strip())
                if m:
                    names.add(m.group(2))
        for name in names:
            name_sources.setdefault(name, set()).add(doc.label)
    renames = {}
    for name, labels in name_sources.items():
        if len(labels) > 1:
            mapped_labels = {}
            for label in labels:
                mapped_labels[label] = label + "__" + name
            renames[name] = mapped_labels
        else:
            only = next(iter(labels))
            renames[name] = dict(((only, name),))
    return renames


def exported_name(renames, name, source):
    return renames.get(name, {}).get(source, name)


@dataclass
class MappedCue:
    clip: Clip
    cue: Cue
    start_ms: int
    end_ms: int
    body_lines: list
    settings: str


def map_time(clip, src_time):
    """Source time -> output timeline Fraction seconds."""
    return clip.out_start + (src_time - clip.src_start) / clip.speed


def remap_body(cue, clip, renames, inter_start, inter_end,
               start_ms, end_ms, where):
    out_lines = []
    for row in cue.body_lines:
        kept_ms = []

        def replace_inline(match):
            t = parse_timestamp(match.group(1),
                                where + ": inline timestamp")
            if not (inter_start < t < inter_end):
                return ""                    # cut away by intersection
            ms = to_ms(map_time(clip, t))
            if not (start_ms < ms < end_ms):
                return ""                    # not strictly inside after quant
            if ms in kept_ms:
                return ""                    # same instant: first survives
            kept_ms.append(ms)
            return "<" + fmt_millis(ms) + ">"

        row = INLINE_TS.sub(replace_inline, row)

        def replace_tag(match):
            name = match.group(1)
            classes = match.group(2)
            rest = match.group(3)
            parts = []
            for cls in classes.lstrip(".").split("."):
                parts.append("." + exported_name(renames, cls, cue.source))
            return "<" + name + "".join(parts) + rest + ">"

        out_lines.append(OPEN_TAG.sub(replace_tag, row))
    return out_lines


def remap_settings(cue, renames):
    def repl(token_match):
        token = token_match.group(0)
        key, _, value = token.partition(":")
        if key == "region":
            return "region:" + exported_name(renames, value, cue.source)
        return token

    return re.sub(r"\S+", repl, cue.settings)


def cut(clips, source_docs, renames):
    mapped = []
    for clip in clips:
        doc = source_docs[clip.source]
        for cue in doc.cues:
            inter_start = max(cue.start, clip.src_start)
            inter_end = min(cue.end, clip.src_end)
            if inter_start >= inter_end:
                continue
            start_ms = to_ms(map_time(clip, inter_start))
            end_ms = to_ms(map_time(clip, inter_end))
            if start_ms >= end_ms:
                continue                      # zero length after quantisation
            where = "%s (%s) line %d" % (doc.label, doc.path, cue.line)
            body = remap_body(cue, clip, renames, inter_start, inter_end,
                              start_ms, end_ms, where)
            mapped.append(MappedCue(
                clip, cue, start_ms, end_ms, body,
                remap_settings(cue, renames),
            ))
    return mapped


def assign_ids(mapped, source_docs):
    """Unique, stable, order-independent cue ids.

    Group by (source, source cue line).  A group appearing once keeps the
    source cue id (anonymous cues get c1, c2, ... per source); groups that
    appear repeatedly get deterministic -2, -3 suffixes ordered by clip source
    position.  Base ids colliding across distinct cues get a source prefix.
    """
    anon_index = {}
    for label, doc in source_docs.items():
        ordered_cues = sorted(doc.cues, key=lambda c: c.line)
        for pos, cue in enumerate(ordered_cues, 1):
            anon_index[(label, cue.line)] = pos

    groups = {}
    for mc in mapped:
        key = (mc.cue.source, mc.cue.line)
        groups.setdefault(key, []).append(mc)

    bases = {}
    owner_of_base = {}
    for key, group in groups.items():
        label, line = key
        cue = group[0].cue
        if cue.source_id is not None:
            base = cue.source_id
        else:
            base = "c%d" % anon_index[(label, line)]
        prior = owner_of_base.get(base)
        if prior is not None and prior != key:
            # identical base id owned by a different cue: prefix both
            bases[prior] = prior[0] + "__" + bases[prior]
            if cue.source_id is not None:
                base = label + "__" + cue.source_id
            else:
                base = label + "__c%d" % anon_index[(label, line)]
        owner_of_base[base] = key
        bases[key] = base

    ids = {}
    used = set()
    for key, group in groups.items():
        base = bases[key]
        ordered = sorted(group,
                         key=lambda mc: (mc.clip.start_frame, mc.clip.id))
        first = ordered[0]
        rest = ordered[1:]
        if base not in used:
            chosen = base
        else:
            chosen = key[0] + "__" + base
        used.add(chosen)
        ids[id(first)] = chosen
        n = 1
        for mc in rest:
            n += 1
            candidate = "%s-%d" % (base, n)
            while candidate in used:
                n += 1
                candidate = "%s-%d" % (base, n)
            used.add(candidate)
            ids[id(mc)] = candidate
    return ids

def render_style(block, source, source_path, renames):
    out = ["STYLE"]
    for rule in block.rules:
        stripped = rule.strip()
        if stripped == "":
            continue
        m = CLASS_SELECTOR.match(stripped)
        if m is None:
            raise CutError(
                "%s (%s) style block at line %d: unsupported rule %r; only "
                "::cue(.class) selectors are supported"
                % (source, source_path, block.line, rule))
        cls = exported_name(renames, m.group(2), source)
        out.append("%s(.%s)%s" % (m.group(1), cls, m.group(3)))
    return out


def render_region(block, source, renames):
    out = ["REGION"]
    for payload in block.lines:
        key, sep, value = payload.partition(":")
        if sep and key.strip() == "id":
            out.append("id:"
                       + exported_name(renames, value.strip(), source))
        else:
            out.append(payload)
    return out


def validate_references(source_docs):
    for doc in source_docs.values():
        for line, refs in doc.region_refs.items():
            for name in refs:
                if name not in doc.region_ids:
                    raise CutError("%s (%s) line %d: cue references missing "
                                   "region %r"
                                   % (doc.label, doc.path, line, name))


def render(mapped, ids, source_docs, clips, renames):
    used_sources = sorted(set(clip.source for clip in clips))
    parts = ["WEBVTT"]
    for label in used_sources:
        doc = source_docs[label]
        for block in doc.styles:
            parts.append("\n".join(
                render_style(block, label, doc.path, renames)))
        for block in doc.regions:
            parts.append("\n".join(render_region(block, label, renames)))

    ordered = sorted(mapped,
                     key=lambda mc: (mc.start_ms, mc.clip.index, mc.cue.line))
    records = []
    for mc in ordered:
        cid = ids[id(mc)]
        timing = (fmt_millis(mc.start_ms) + " --> "
                  + fmt_millis(mc.end_ms) + mc.settings)
        parts.append("\n".join([cid, timing] + mc.body_lines))
        records.append(dict(
            id=cid,
            clip=mc.clip.id,
            source=mc.cue.source,
            source_id=mc.cue.source_id,
            line=mc.cue.line,
            start_ms=mc.start_ms,
            end_ms=mc.end_ms,
        ))
    return "\n\n".join(parts) + "\n", dict(cues=records)


def run(edl_path):
    edl_path = Path(edl_path)
    sources, clips = load_edl(edl_path)
    source_docs = {}
    for label, rel in sources.items():
        source_docs[label] = parse_vtt(edl_path.parent / rel, label)
    validate_references(source_docs)
    renames = build_renames(source_docs)
    mapped = cut(clips, source_docs, renames)
    ids = assign_ids(mapped, source_docs)
    return render(mapped, ids, source_docs, clips, renames)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Cut WebVTT sources by an EDL")
    parser.add_argument("edl", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("map", type=Path)
    args = parser.parse_args(argv)
    try:
        vtt_text, cue_map = run(args.edl)
    except CutError as exc:
        print("cut_vtt: " + str(exc), file=sys.stderr)
        return 2
    # all validation happens before any write; temp-then-replace keeps any
    # existing output intact if a write itself fails
    tmp_vtt = args.output.with_name(args.output.name + ".tmp")
    tmp_map = args.map.with_name(args.map.name + ".tmp")
    tmp_vtt.write_text(vtt_text, encoding="utf-8")
    tmp_map.write_text(json.dumps(cue_map, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    tmp_vtt.replace(args.output)
    tmp_map.replace(args.map)
    return 0
