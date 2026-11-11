"""Independent batch reference implementation.

Reads a whole (sealed) source at once and classifies every line.  Shares no
code with the streaming pipeline; the differential tests and the `verify`
CLI command cross-check the pipeline's stored records and reject
classifications against this implementation.
"""

import json
import re
from decimal import Decimal

AMOUNT_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
TWO_PLACES = Decimal("0.01")


class _DupKey(Exception):
    pass


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DupKey(key)
        result[key] = value
    return result


def _classify(line, norm_version):
    """Return ("R", fields-dict) or ("J", reason-code)."""
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return ("J", "invalid_utf8")
    if len(text.strip()) == 0:
        return ("J", "empty_line")
    try:
        value = json.loads(text, object_pairs_hook=_strict_object)
    except _DupKey:
        return ("J", "duplicate_key")
    except ValueError:
        return ("J", "invalid_json")
    if not isinstance(value, dict):
        return ("J", "not_an_object")

    ver = value.get("version")
    if "version" not in value:
        return ("J", "missing_field")
    if not isinstance(ver, int) or isinstance(ver, bool):
        return ("J", "invalid_field")
    if ver not in (1, 2):
        return ("J", "unknown_version")

    if "id" not in value:
        return ("J", "missing_field")
    rid = value["id"]
    if not isinstance(rid, str) or len(rid) == 0:
        return ("J", "invalid_field")

    if "currency" not in value:
        if norm_version < 2:
            return ("J", "missing_field")
        currency = "CNY"
    else:
        currency = value["currency"]
    if not isinstance(currency, str) or CURRENCY_PATTERN.match(currency) is None:
        return ("J", "invalid_field")

    if ver == 1:
        if "amount" not in value:
            return ("J", "missing_field")
        raw_amount = value["amount"]
        if not isinstance(raw_amount, str) or AMOUNT_PATTERN.match(raw_amount) is None:
            return ("J", "invalid_field")
        amount = Decimal(raw_amount)
        if -amount.as_tuple().exponent > 2:
            return ("J", "amount_precision")
        amount = amount.quantize(TWO_PLACES)
    else:
        if "amount_cents" not in value:
            return ("J", "missing_field")
        cents = value["amount_cents"]
        if not isinstance(cents, int) or isinstance(cents, bool):
            return ("J", "invalid_field")
        amount = (Decimal(cents) * TWO_PLACES).quantize(TWO_PLACES)

    if "note" not in value:
        note_state, note = "missing", None
    elif value["note"] is None:
        note_state, note = "null", None
    elif isinstance(value["note"], str):
        note_state, note = "present", value["note"]
    else:
        return ("J", "invalid_field")

    return ("R", {"id": rid, "amount": str(amount), "currency": currency,
                  "note_state": note_state, "note": note})


def reference_rows(data, norm_version=1):
    """Classify a whole sealed source (bytes).  Returns the canonical row
    list: ("R", offset, id, amount, currency, note_state, note, norm_version)
    or ("J", offset, reason_code), ordered by offset."""
    rows = []
    offset = 0
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()  # trailing newline terminates the last line
    for line in parts:
        kind, payload = _classify(line, norm_version)
        if kind == "R":
            rows.append(("R", offset, payload["id"], payload["amount"],
                         payload["currency"], payload["note_state"],
                         payload["note"], norm_version))
        else:
            rows.append(("J", offset, payload))
        offset += len(line) + 1
    return rows


def canonical_rows(store, source_id):
    """The pipeline's stored rows in the same canonical shape."""
    rows = []
    for (off, nv, rid, amount, cur, ns, note) in store.iter_records(source_id):
        rows.append(("R", off, rid, amount, cur, ns, note, nv))
    for (off, reason, _detail) in store.iter_rejects(source_id):
        rows.append(("J", off, reason))
    rows.sort(key=lambda row: row[1])
    return rows
