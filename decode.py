"""Decode migrated/archived web bytes whose encoding hints disagree.

Entry point:  python3 decode.py input.json output.json

Only the Python standard library is used.

Candidate encodings are tried, in order, from:
    BOM -> HTTP charset -> HTML <meta> charset -> probe list -> fallback list.
Every *canonical* codec is tried at most once per document; a candidate is
accepted only when strict decoding succeeds.  Unknown labels are skipped and
recorded as "unknown".  When every candidate fails, the bytes are recovered as
UTF-8: decodable fragments are kept, every offending byte is listed in
``bad_bytes`` and rendered in the text as U+FFFD (one replacement char per
bad byte).
"""

import argparse
import codecs
import json
from pathlib import Path

# BOM byte prefix -> (canonical codec used to decode, number of BOM bytes).
# UTF-16 LE/BE are matched explicitly before the generic UTF-16 prefix so the
# byte order is unambiguous even for an odd-length (invalid) tail.
_BOM_TABLE = (
    (codecs.BOM_UTF32_LE, "utf-32-le", 4),
    (codecs.BOM_UTF32_BE, "utf-32-be", 4),
    (codecs.BOM_UTF16_LE, "utf-16-le", 2),
    (codecs.BOM_UTF16_BE, "utf-16-be", 2),
    (codecs.BOM_UTF8, "utf-8", 3),
)

# Source labels, in a fixed order so the statistics block is reproducible.
_SOURCES = ("bom", "http", "meta", "probe", "fallback", "recovery")


def canonical_name(label):
    """Return the canonical Python codec name for ``label``.

    Returns ``None`` when the label is missing/empty or unknown to the codec
    registry (the caller records such a label as "unknown").
    """
    if label is None:
        return None
    label = str(label).strip()
    if not label:
        return None
    try:
        return codecs.lookup(label).name
    except LookupError:
        return None


def detect_bom(body):
    """Return ``(canonical_codec, payload_without_bom)`` or ``None``."""
    for prefix, codec, size in _BOM_TABLE:
        if body.startswith(prefix):
            return codec, body[size:]
    return None


def recover_utf8(body):
    """Recover decodable UTF-8 fragments from an undecodable byte string.

    Each bad byte is appended to ``bad_bytes`` and replaced by one U+FFFD.
    The spans reported by the strict decoder are expanded byte-by-byte so the
    number of replacement characters always equals the number of bad bytes.
    """
    pieces = []
    bad_bytes = []
    offset = 0
    while offset < len(body):
        try:
            body[offset:].decode("utf-8", errors="strict")
            pieces.append(body[offset:].decode("utf-8", errors="strict"))
            break
        except UnicodeDecodeError as err:
            # Bytes before the offending span are valid text.
            pieces.append(body[offset:offset + err.start].decode("utf-8"))
            # Every byte in the offending span is bad -> one U+FFFD each.
            for byte in body[offset + err.start:offset + err.end]:
                bad_bytes.append(byte)
                pieces.append("�")
            offset += err.end
    return "".join(pieces), bad_bytes


def decode_document(document, probe, fallback):
    """Resolve one document to a result dictionary."""
    body = bytes.fromhex(document["body_hex"])
    attempts = []          # [raw_label, result] in real attempt order
    tried = set()          # canonical codec names already attempted
    accepted = None        # (text, canonical_codec, source)

    def try_codec(label, source):
        """Attempt one labelled codec; return True when it is accepted."""
        canonical = canonical_name(label)
        if canonical is None:
            # Unknown/empty label: record and skip (no canonical codec used).
            if label is not None and str(label).strip():
                attempts.append([str(label), "unknown"])
            return False
        if canonical in tried:
            # Each canonical codec is attempted at most once.
            return False
        tried.add(canonical)
        try:
            text = body.decode(canonical, errors="strict")
        except (UnicodeDecodeError, UnicodeEncodeError):
            attempts.append([canonical, "invalid"])
            return False
        attempts.append([canonical, "ok"])
        return text, canonical, source

    # 1) BOM: the BOM itself is removed before strict decoding.
    bom = detect_bom(body)
    if bom is not None:
        bom_codec, payload = bom
        tried.add(bom_codec)
        try:
            text = payload.decode(bom_codec, errors="strict")
        except UnicodeDecodeError:
            attempts.append([bom_codec, "invalid"])
        else:
            attempts.append([bom_codec, "ok"])
            accepted = (text, bom_codec, "bom")

    # 2) Ordered labelled candidates: HTTP, meta, probe list, fallback list.
    if accepted is None:
        labelled = []
        labelled.append((document.get("http_charset"), "http"))
        labelled.append((document.get("meta_charset"), "meta"))
        labelled.extend((label, "probe") for label in probe)
        labelled.extend((label, "fallback") for label in fallback)

        for label, source in labelled:
            if label is None or not str(label).strip():
                continue
            outcome = try_codec(label, source)
            if outcome is not False:
                accepted = outcome
                break

    # 3) Recovery: every candidate failed (or no candidate existed).
    if accepted is None:
        text, bad_bytes = recover_utf8(body)
        return {
            "id": document["id"],
            "text": text,
            "encoding": None,
            "source": "recovery",
            "bad_bytes": bad_bytes,
            "attempts": attempts,
        }

    text, encoding, source = accepted
    return {
        "id": document["id"],
        "text": text,
        "encoding": encoding,
        "source": source,
        "bad_bytes": [],
        "attempts": attempts,
    }


def process(data):
    probe = data.get("probe") or []
    fallback = data.get("fallback") or []

    results = [
        decode_document(document, probe, fallback)
        for document in data["documents"]
    ]
    # Output order is independent of the input document order.
    results.sort(key=lambda item: item["id"])

    by_source = {source: 0 for source in _SOURCES}
    total_bad_bytes = 0
    for result in results:
        by_source[result["source"]] += 1
        total_bad_bytes += len(result["bad_bytes"])

    return {
        "documents": results,
        "summary": {
            "by_source": by_source,
            "bad_bytes_total": total_bad_bytes,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Decode archived web bytes with conflicting charset hints."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    result = process(data)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
