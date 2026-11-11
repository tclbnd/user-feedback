"""Test corpus: generated legal records plus fixed rejection samples.

Coverage-feedback rule (from the task): feedback from differential runs is
used first to expand *legal* synthetic corpus structures and chunk-split
combinations; business rejections live in a fixed, hand-authored sample list
so rejection classification never silently drifts with the generator.
"""

import json
import random
from typing import List, Tuple

# Fixed rejection samples: (label, raw line bytes, expected reason code).
# These rows are deliberately NOT generated -- they pin every documented rule.
FIXED_REJECTS: List[Tuple[str, bytes, str]] = [
    ("syntax error", b'{"version":1,"id":"R-01","amount":"1.00",', "json_syntax_error"),
    ("duplicate key", b'{"version":1,"id":"R-02","amount":"1.00","id":"R-02X"}', "duplicate_key"),
    ("duplicate nested key", b'{"version":1,"id":"R-03","amount":"1.00","meta":{"a":1,"a":2}}', "duplicate_key"),
    ("top level scalar", b'42', "not_object"),
    ("missing id", b'{"version":1,"amount":"1.00","currency":"CNY"}', "missing_field"),
    ("null id", b'{"version":1,"id":null,"amount":"1.00","currency":"CNY"}', "null_field"),
    ("missing currency", b'{"version":1,"id":"R-04","amount":"1.00"}', "missing_field"),
    ("bad currency", b'{"version":1,"id":"R-05","amount":"1.00","currency":"cny"}', "currency_format"),
    ("missing amount v1", b'{"version":1,"id":"R-06","currency":"CNY"}', "missing_field"),
    ("null amount", b'{"version":1,"id":"R-07","amount":null,"currency":"CNY"}', "null_field"),
    ("amount float type", b'{"version":1,"id":"R-08","amount":1.5,"currency":"CNY"}', "bad_type"),
    ("amount non decimal", b'{"version":1,"id":"R-09","amount":"1.2x","currency":"CNY"}', "amount_format"),
    ("amount exponent", b'{"version":1,"id":"R-10","amount":"1E2","currency":"CNY"}', "amount_format"),
    ("amount precision", b'{"version":1,"id":"R-11","amount":"0.001","currency":"CNY"}', "amount_precision"),
    ("amount range", b'{"version":1,"id":"R-12","amount":"99999999999.00","currency":"CNY"}', "amount_range"),
    ("v2 missing cents", b'{"version":2,"id":"R-13","currency":"CNY"}', "missing_field"),
    ("v2 cents float", b'{"version":2,"id":"R-14","amount_cents":12.5,"currency":"CNY"}', "bad_type"),
    ("v2 cents bool", b'{"version":2,"id":"R-15","amount_cents":true,"currency":"CNY"}', "bad_type"),
    ("v2 cents range", b'{"version":2,"id":"R-16","amount_cents":9999999999999,"currency":"CNY"}', "amount_range"),
    ("id bool", b'{"version":1,"id":true,"amount":"1.00","currency":"CNY"}', "bad_type"),
    ("version bool", b'{"version":true,"id":"R-17","amount":"1.00","currency":"CNY"}', "bad_type"),
    ("unknown version", b'{"version":9,"id":"R-18","amount":"1.00","currency":"CNY"}', "unknown_version"),
    ("note bad type", b'{"version":1,"id":"R-19","amount":"1.00","currency":"CNY","note":7}', "bad_type"),
    ("empty id", b'{"version":1,"id":"","amount":"1.00","currency":"CNY"}', "bad_type"),
]

# A byte sequence that is never valid UTF-8 regardless of chunk boundaries.
BAD_UTF8_LABEL = "invalid utf-8"
BAD_UTF8_BYTES = b'{"version":1,"id":"R-20","amount":"1.00","currency":"CNY","note":"\xff\xfe"}'
BAD_UTF8_REASON = "encoding_error"

CURRENCIES = ["CNY", "USD", "EUR", "JPY", "GBP"]
NOTES = ["蓝莓", "café", "λ函数", "emoji 🫐 测试", "plain note", "  spaced  ", "引号\"和\\反斜杠"]


def legal_record(rng: random.Random, index: int) -> dict:
    """Generate one structurally diverse, always-legal record."""
    version = rng.choice((1, 2))
    record = {"version": version, "id": f"S-{index:05d}"}

    if version == 1:
        # exercise padding, zero, negatives and the 2-decimal boundary
        style = rng.choice(("padded", "integer", "zero", "negative", "boundary"))
        if style == "padded":
            record["amount"] = f"{rng.randint(0, 9999)}.{rng.randint(0, 99):02d}"
        elif style == "integer":
            record["amount"] = str(rng.randint(0, 100000))
        elif style == "zero":
            record["amount"] = rng.choice(("0", "0.00", "-0.00"))
        elif style == "negative":
            record["amount"] = f"-{rng.randint(1, 500)}.{rng.randint(0, 99):02d}"
        else:
            record["amount"] = "9999999999.99"
    else:
        cents_style = rng.choice(("normal", "zero", "negative", "boundary"))
        if cents_style == "normal":
            record["amount_cents"] = rng.randint(0, 10_000_000)
        elif cents_style == "zero":
            record["amount_cents"] = 0
        elif cents_style == "negative":
            record["amount_cents"] = -rng.randint(1, 9999)
        else:
            record["amount_cents"] = 999_999_999_999

    record["currency"] = rng.choice(CURRENCIES)

    # note: absent, explicit null, or a string -- the three distinct states
    note_style = rng.choice(("absent", "null", "unicode", "ascii"))
    if note_style == "null":
        record["note"] = None
    elif note_style == "unicode":
        record["note"] = rng.choice(NOTES)
    elif note_style == "ascii":
        record["note"] = f"note-{index}"
    # 'absent': key intentionally missing

    # unknown forward-compatible fields must be ignored by both implementations
    if rng.random() < 0.3:
        record["future_field"] = rng.choice(["x", 1, True, None, {"k": "v"}, [1, 2]])
    return record


def legal_ndjson(seed: int, n: int) -> bytes:
    """Generate ``n`` legal NDJSON lines, optionally with blank lines/CRLF."""
    rng = random.Random(seed)
    chunks: List[bytes] = []
    for i in range(n):
        line = json.dumps(legal_record(rng, i), ensure_ascii=False).encode("utf-8")
        if rng.random() < 0.15:
            line = line.replace(b":", b": ", 1)  # varied whitespace, same meaning
        chunks.append(line)
        if rng.random() < 0.08:
            chunks.append(b"")  # blank physical line
    terminator = b"\r\n" if rng.random() < 0.25 else b"\n"
    return terminator.join(chunks) + terminator


def fixed_reject_ndjson() -> bytes:
    """All fixed rejection samples concatenated as one NDJSON source."""
    return b"\n".join(row for _, row, _ in FIXED_REJECTS) + b"\n" + BAD_UTF8_BYTES + b"\n"


def expected_reject_reasons() -> List[str]:
    reasons = [reason for _, _, reason in FIXED_REJECTS]
    reasons.append(BAD_UTF8_REASON)
    return reasons


def chunk_byte_sizes(seed: int, data_len: int) -> List[int]:
    """Pseudorandom chunk sizes that stay within the data length."""
    rng = random.Random(seed)
    sizes = []
    remaining = data_len
    while remaining > 0:
        size = rng.choice((1, 2, 3, 5, 7, 16, 31, 64, 127, 256, 1024))
        size = min(size, remaining)
        sizes.append(size)
        remaining -= size
    return sizes


def split_at_offsets(data: bytes, offsets: List[int]) -> List[bytes]:
    """Split a byte stream at given absolute offsets (offsets need not align)."""
    points = [0] + offsets + [len(data)]
    return [data[points[i]:points[i + 1]] for i in range(len(points) - 1) if points[i] != points[i + 1]]
