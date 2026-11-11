"""Synthetic corpus generation with coverage feedback.

Only *valid* records are generated; business rejects come from the fixed
sample file fixtures/reject-samples.ndjson and are interleaved at fixed
positions.  The generator explores combinations of:

  chunk_size  read-chunk sizes, including 1-byte (splits UTF-8 chars)
  versions    which record versions appear
  notes       present / null / missing note mixes
  unicode     ascii / CJK / emoji content
  amounts     zero / negative / 2dp-max / large values
  line_len    short lines and lines longer than the read buffer
  appends     number of append rounds the source arrives in
  replays     extra full replays after sealing (must be no-ops)
  rewind      restore an old checkpoint and replay from it
  seal_tail   whether the sealed source ends with a newline

Coverage (how often each axis value has been exercised) is persisted to
artifacts/coverage.json; each round prefers the least-covered values, so
structure and chunk-split combinations expand where coverage is thinnest.
"""

import json
import os
import random
import tempfile

from . import importer
from .reference import canonical_rows, reference_rows
from .store import Store

AXES = {
    "chunk_size": [1, 2, 3, 5, 17, 64, 4096],
    "versions": ["v1", "v2", "mixed"],
    "notes": ["present", "null", "missing", "mixed"],
    "unicode": ["ascii", "cjk", "emoji", "mixed"],
    "amounts": ["zero", "two_dp", "negative", "big", "mixed"],
    "line_len": ["short", "long"],
    "appends": [1, 2, 3, 5],
    "replays": [0, 1, 3],
    "rewind": [0, 1],
    "seal_tail": ["clean", "partial"],
}

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fixtures")


def load_reject_samples():
    with open(os.path.join(FIXTURES, "reject-samples.ndjson"), "rb") as fh:
        data = fh.read()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


# -- coverage feedback --------------------------------------------------

def load_coverage(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_coverage(path, coverage):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(coverage, fh, indent=1, sort_keys=True)


def _key(axis, value):
    return "%s=%s" % (axis, value)


def pick_combo(rng, coverage, candidates=64):
    """Pick the least-covered random combination of axis values."""
    best, best_score = None, None
    for _ in range(candidates):
        combo = {axis: rng.choice(values) for axis, values in AXES.items()}
        score = sum(coverage.get(_key(axis, value), 0)
                    for axis, value in combo.items())
        if best_score is None or score < best_score:
            best, best_score = combo, score
    return best


def record_coverage(coverage, combo):
    for axis, value in combo.items():
        key = _key(axis, value)
        coverage[key] = coverage.get(key, 0) + 1


# -- corpus generation ----------------------------------------------------

def _text(rng, mode, length):
    pools = {
        "ascii": "abcdefgh0123456789 ",
        "cjk": "蓝莓苹果香蕉橙子公司供应商",
        "emoji": "🍇🍎🍊🚀✨",
        "mixed": "ab蓝莓🍇 x9",
    }
    pool = pools[mode]
    return "".join(rng.choice(pool) for _ in range(length))


def _amount_fields(rng, version, mode):
    def cents_value():
        if mode == "zero":
            return 0
        if mode == "negative":
            return -rng.randint(1, 99999)
        if mode == "big":
            return rng.randint(10**12, 10**15)
        if mode == "two_dp":
            return rng.randint(1, 999999)
        return rng.randint(-10**6, 10**9)  # mixed
    cents = cents_value()
    if version == 2:
        return {"amount_cents": cents}
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return {"amount": "%s%d.%02d" % (sign, cents // 100, cents % 100)}


def gen_valid_record(rng, combo, seq):
    version = {"v1": 1, "v2": 2}.get(combo["versions"]) or rng.choice((1, 2))
    note_mode = combo["notes"]
    if note_mode == "mixed":
        note_mode = rng.choice(("present", "null", "missing"))
    obj = {"version": version,
           "id": "%s-%05d" % (_text(rng, combo["unicode"], 2), seq)}
    obj.update(_amount_fields(rng, version, combo["amounts"]))
    obj["currency"] = rng.choice(("CNY", "USD", "JPY"))
    if note_mode == "present":
        length = 4000 if combo["line_len"] == "long" else rng.randint(0, 12)
        obj["note"] = _text(rng, combo["unicode"], max(1, length))
    elif note_mode == "null":
        obj["note"] = None
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_corpus_lines(rng, combo, count=40):
    """Valid generated lines with fixed reject samples interleaved."""
    samples = load_reject_samples()
    lines = []
    sample_idx = 0
    for seq in range(count):
        lines.append(gen_valid_record(rng, combo, seq).encode("utf-8"))
        if seq % 7 == 3:  # fixed positions, fixed samples, cycled
            lines.append(samples[sample_idx % len(samples)])
            sample_idx += 1
    return lines


def _split_parts(rng, data, n_parts):
    if n_parts <= 1 or len(data) < 2:
        return [data]
    cuts = sorted(rng.sample(range(1, len(data)),
                             min(n_parts - 1, len(data) - 1)))
    parts, prev = [], 0
    for cut in cuts:
        parts.append(data[prev:cut])
        prev = cut
    parts.append(data[prev:])
    return parts


def run_case(combo, rng):
    """Run one generated corpus through the streaming pipeline and assert
    it matches the batch reference.  Returns a summary dict."""
    lines = build_corpus_lines(rng, combo)
    data = b"\n".join(lines)
    if combo["seal_tail"] == "clean":
        data += b"\n"
    parts = _split_parts(rng, data, combo["appends"])

    with tempfile.TemporaryDirectory(prefix="ndjson-case-") as tmp:
        src = os.path.join(tmp, "source.ndjson")
        open(src, "wb").close()
        store = Store(os.path.join(tmp, "case.db"))
        try:
            for idx, part in enumerate(parts):
                with open(src, "ab") as fh:
                    fh.write(part)
                importer.ingest(store, "src", src,
                                chunk_size=combo["chunk_size"],
                                seal=(idx == len(parts) - 1))
            for _ in range(combo["replays"]):
                stats = importer.ingest(store, "src", src,
                                        chunk_size=combo["chunk_size"],
                                        seal=True)
                assert stats.records == 0 and stats.rejects == 0
            if combo["rewind"] and len(lines) > 2:
                offsets = [0]
                for line in lines[:-1]:
                    offsets.append(offsets[-1] + len(line) + 1)
                store.reset_checkpoint(
                    "src", offsets[rng.randrange(1, len(offsets))])
                importer.ingest(store, "src", src,
                                chunk_size=combo["chunk_size"], seal=True)

            ckpt = store.get_checkpoint("src")
            assert ckpt == (len(data), 1), (ckpt, len(data))
            assert store.count("conflicts", "src") == 0

            got = canonical_rows(store, "src")
            want = reference_rows(data)
            if got != want:
                raise AssertionError(_diff(got, want))
            return {"lines": len(lines), "bytes": len(data),
                    "records": store.count("records", "src"),
                    "rejects": store.count("rejects", "src")}
        finally:
            store.close()


def _diff(got, want):
    out = ["differential mismatch:"]
    for idx, (g, w) in enumerate(zip(got, want)):
        if g != w:
            out.append("  first diff at row %d:\n    got  %r\n    want %r"
                       % (idx, g, w))
            break
    out.append("  lengths: got %d, want %d" % (len(got), len(want)))
    return "\n".join(out)
