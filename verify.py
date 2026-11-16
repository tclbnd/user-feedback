"""Acceptance checks for decode.py against the stated requirements.

Runs the decoder on both fallback sets, on a shuffled-copy input, and asserts:
  * >=30 documents, output sorted by id and identical regardless of order;
  * every document has the required fields;
  * attempts follow the real BOM -> HTTP -> meta -> probe -> fallback order;
  * unknown labels are logged as "unknown" with the raw label;
  * each canonical codec is attempted at most once;
  * strict decoding is the acceptance rule;
  * recovery emits one U+FFFD per bad byte and lists every bad byte;
  * summary by_source counts and bad_bytes_total are consistent;
  * BOM text is decoded with the BOM stripped;
  * both fallback sets and input reordering give reproducible results.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import codecs

ROOT = Path(__file__).resolve().parent
DECODER = ROOT / "decode.py"
SOURCES = ("bom", "http", "meta", "probe", "fallback", "recovery")
failures = []


# ---- independent re-implementation helpers -------------------------------
def canonical_or_none(label):
    if not label or not str(label).strip():
        return None
    try:
        return codecs.lookup(str(label).strip()).name
    except LookupError:
        return None


def bom_info(body):
    table = (
        (codecs.BOM_UTF32_LE, "utf-32-le", 4),
        (codecs.BOM_UTF32_BE, "utf-32-be", 4),
        (codecs.BOM_UTF16_LE, "utf-16-le", 2),
        (codecs.BOM_UTF16_BE, "utf-16-be", 2),
        (codecs.BOM_UTF8, "utf-8", 3),
    )
    for prefix, codec, size in table:
        if body.startswith(prefix):
            return codec, body[size:]
    return None


def recover_reference(body):
    pieces, bad = [], []
    offset = 0
    while offset < len(body):
        try:
            body[offset:].decode("utf-8", "strict")
            pieces.append(body[offset:].decode("utf-8"))
            break
        except UnicodeDecodeError as err:
            pieces.append(body[offset:offset + err.start].decode("utf-8"))
            for byte in body[offset + err.start:offset + err.end]:
                bad.append(byte)
                pieces.append("�")
            offset += err.end
    return "".join(pieces), bad


def simulate_attempts(meta, body, probe, fallback):
    """Independent model of the decoder's attempt sequence."""
    attempts = []
    tried = set()

    def run(codec, payload):
        tried.add(codec)
        try:
            payload.decode(codec, "strict")
        except UnicodeDecodeError:
            attempts.append([codec, "invalid"])
            return False
        attempts.append([codec, "ok"])
        return True

    bom = bom_info(body)
    accepted = False
    if bom is not None:
        accepted = run(bom[0], bom[1])

    if not accepted:
        candidates = []
        candidates.append(meta.get("http_charset"))
        candidates.append(meta.get("meta_charset"))
        candidates.extend(probe)
        candidates.extend(fallback)
        for label in candidates:
            if label is None or not str(label).strip():
                continue
            canonical = canonical_or_none(label)
            if canonical is None:
                attempts.append([str(label), "unknown"])
                continue
            if canonical in tried:
                continue
            if run(canonical, body):
                accepted = True
                break
    return attempts


def check(condition, message):
    if condition:
        print("  ok:", message)
    else:
        failures.append(message)
        print("FAIL:", message)


def run(input_obj, tag):
    tmp_dir = Path(tempfile.mkdtemp(prefix="decode_verify_"))
    in_path = tmp_dir / ("verify_%s_in.json" % tag)
    out_path = tmp_dir / ("verify_%s_out.json" % tag)
    in_path.write_text(json.dumps(input_obj, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(DECODER), str(in_path), str(out_path)],
        check=True,
    )
    return json.loads(out_path.read_text(encoding="utf-8"))


def load_input(tag):
    return json.loads((ROOT / ("input_%s.json" % tag)).read_text(encoding="utf-8"))


for set_tag in ("a", "b"):
    print("\n=== fallback set %s ===" % set_tag.upper())
    source = load_input(set_tag)
    docs = source["documents"]
    probe = source["probe"]
    fallback = source["fallback"]
    check(len(docs) >= 30, "at least 30 local samples (got %d)" % len(docs))

    result = run(source, set_tag)
    out_docs = result["documents"]

    check([d["id"] for d in out_docs] == sorted(d["id"] for d in docs),
          "documents are sorted by id")
    check(len(out_docs) == len(docs), "one output per input document")

    required = {"id", "text", "encoding", "source", "bad_bytes", "attempts"}
    check(all(required == set(d.keys()) for d in out_docs),
          "every document exposes exactly id/text/encoding/source/bad_bytes/attempts")
    check(all(d["source"] in SOURCES for d in out_docs),
          "all source labels are in bom/http/meta/probe/fallback/recovery")

    index = {d["id"]: d for d in out_docs}
    total_bad = 0
    for meta in docs:
        out = index[meta["id"]]
        body = bytes.fromhex(meta["body_hex"])
        bom = bom_info(body)
        attempts = out["attempts"]

        # attempts are [label, result] pairs, result in ok/invalid/unknown.
        check(all(len(a) == 2 and a[1] in ("ok", "invalid", "unknown")
                  for a in attempts), "%s: attempt results are ok/invalid/unknown" % meta["id"])
        # known labels are rendered with their canonical codec name.
        for label, verdict in attempts:
            if verdict != "unknown":
                check(codecs.lookup(label).name == label,
                      "%s: valid label %r is canonical" % (meta["id"], label))
        # unknown labels keep the raw spelling, but only labels reached before
        # the first success are attempted (the chain short-circuits).
        raw_unknown = [l for l in (meta.get("http_charset"), meta.get("meta_charset"),
                                   *probe, *fallback)
                       if l and canonical_or_none(l) is None]
        logged_unknown = [l for l, v in attempts if v == "unknown"]
        check(all(l in logged_unknown for l in raw_unknown[:len(logged_unknown)]),
              "%s: reached unknown labels are logged" % meta["id"])

        # each canonical codec appears at most once across attempts.
        canon_attempts = [l for l, v in attempts if v != "unknown"]
        check(len(canon_attempts) == len(set(canon_attempts)),
              "%s: each canonical codec attempted at most once" % meta["id"])

        # exactly one accepted (ok) attempt unless this is a recovery document.
        ok_count = sum(1 for _, v in attempts if v == "ok")
        if out["source"] == "recovery":
            check(ok_count == 0, "%s: recovery has no successful codec" % meta["id"])
            check(out["encoding"] is None, "%s: recovery encoding is null" % meta["id"])
        else:
            check(ok_count == 1, "%s: exactly one successful codec" % meta["id"])
            check(out["encoding"] == [l for l, v in attempts if v == "ok"][0],
                  "%s: encoding matches the ok attempt" % meta["id"])

        # attempt order: BOM first, then http, meta, probe, fallback, with the
        # same short-circuiting and canonical de-duplication as the decoder.
        expected_attempts = simulate_attempts(meta, body, probe, fallback)
        check(expected_attempts == attempts,
              "%s: attempts match simulated real candidate order" % meta["id"])

        # strict acceptance: the reported encoding must strictly decode body
        # (or the BOM-stripped payload) without error.
        if out["source"] != "recovery":
            payload = bom[1] if out["source"] == "bom" else body
            try:
                strict_text = payload.decode(out["encoding"], "strict")
                check(strict_text == out["text"],
                      "%s: text equals strict decode of accepted codec" % meta["id"])
            except UnicodeDecodeError:
                check(False, "%s: accepted codec must strict-decode" % meta["id"])

        # BOM must be stripped from recovered text.
        if out["source"] == "bom":
            check(not out["text"].startswith("﻿")
                  and not out["text"].startswith("￾"),
                  "%s: BOM removed before decode" % meta["id"])
            check(bom[0] == out["encoding"],
                  "%s: BOM codec reported as encoding" % meta["id"])

        # recovery contract: one U+FFFD per bad byte, fragments preserved.
        if out["source"] == "recovery":
            check(out["text"].count("�") == len(out["bad_bytes"]),
                  "%s: one U+FFFD per bad byte" % meta["id"])
            check(all(0 <= b <= 255 for b in out["bad_bytes"]),
                  "%s: bad_bytes are byte values" % meta["id"])
            frag_text, frag_bad = recover_reference(body)
            check(frag_text == out["text"] and frag_bad == out["bad_bytes"],
                  "%s: recovery matches independent reference" % meta["id"])
            total_bad += len(out["bad_bytes"])
        else:
            check(out["bad_bytes"] == [], "%s: non-recovery has no bad bytes" % meta["id"])

    # summary consistency.
    by_source = result["summary"]["by_source"]
    check(set(by_source.keys()) == set(SOURCES), "summary lists all six sources")
    counted = {s: sum(1 for d in out_docs if d["source"] == s) for s in SOURCES}
    check(by_source == counted, "by_source counts match documents")
    check(result["summary"]["bad_bytes_total"] == total_bad,
          "bad_bytes_total equals sum of per-document bad bytes")

    # reproducibility: running again yields byte-identical output.
    again = run(source, set_tag + "_again")
    check(json.dumps(again, ensure_ascii=False, sort_keys=True)
          == json.dumps(result, ensure_ascii=False, sort_keys=True),
          "repeated run on fallback %s is identical" % set_tag.upper())

    # input reordering must not change the per-document results.
    shuffled = dict(source)
    shuffled_docs = list(docs)
    shuffled_docs.reverse()
    shuffled["documents"] = shuffled_docs
    reordered = run(shuffled, set_tag + "_shuffled")
    check(json.dumps(reordered, ensure_ascii=False, sort_keys=True)
          == json.dumps(result, ensure_ascii=False, sort_keys=True),
          "shuffled input on fallback %s gives the same output" % set_tag.upper())

print("\n=== %s ===" % ("ALL CHECKS PASSED" if not failures else "FAILURES"))
for message in failures:
    print(" -", message)
sys.exit(1 if failures else 0)
