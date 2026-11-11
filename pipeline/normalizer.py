"""Versioned normalization rules for supplier NDJSON records.

Explicit classification rules (reject reason codes):

  invalid_utf8      line bytes are not valid UTF-8
  empty_line        line is empty or whitespace only
  invalid_json      line is not well-formed JSON
  not_an_object     top-level JSON value is not an object
  duplicate_key     an object (at any depth) contains a repeated key
  missing_field     a required field is absent
  invalid_field     a field has the wrong type or shape
  unknown_version   record "version" is not supported
  amount_precision  v1 amount string has more than 2 fractional digits

Normalization versions (norm_version) are recorded per stored row.  Replay
never re-normalizes existing rows, so introducing a new normalization
version cannot change the meaning of historical records.

  norm_version 1: missing "currency" is rejected (missing_field)
  norm_version 2: missing "currency" defaults to "CNY"
"""

import json
import re
from decimal import Decimal

CURRENT_NORM_VERSION = 1

INVALID_UTF8 = "invalid_utf8"
EMPTY_LINE = "empty_line"
INVALID_JSON = "invalid_json"
NOT_AN_OBJECT = "not_an_object"
DUPLICATE_KEY = "duplicate_key"
MISSING_FIELD = "missing_field"
INVALID_FIELD = "invalid_field"
UNKNOWN_VERSION = "unknown_version"
AMOUNT_PRECISION = "amount_precision"

NOTE_PRESENT = "present"
NOTE_NULL = "null"
NOTE_MISSING = "missing"

_AMOUNT_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_CENT = Decimal("0.01")

_MISSING = object()


class NormResult:
    __slots__ = ("ok", "reason_code", "detail", "rec_id", "amount",
                 "currency", "note_state", "note")

    def __init__(self, ok, reason_code=None, detail=None, rec_id=None,
                 amount=None, currency=None, note_state=None, note=None):
        self.ok = ok
        self.reason_code = reason_code
        self.detail = detail
        self.rec_id = rec_id
        self.amount = amount          # Decimal, exactly 2 decimal places
        self.currency = currency
        self.note_state = note_state  # present | null | missing
        self.note = note


def _reject(code, detail):
    return NormResult(False, reason_code=code, detail=detail)


class _DuplicateKey(Exception):
    def __init__(self, key):
        super().__init__(key)
        self.key = key


def _object_no_dupes(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKey(key)
        obj[key] = value
    return obj


def normalize_line(raw, norm_version):
    """Normalize one raw NDJSON line (bytes, without the newline).

    Never raises for bad input; bad input becomes a structured reject.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _reject(INVALID_UTF8, "bytes %d..%d" % (exc.start, exc.end))
    if text.strip() == "":
        return _reject(EMPTY_LINE, "empty or whitespace-only line")
    try:
        obj = json.loads(text, object_pairs_hook=_object_no_dupes)
    except _DuplicateKey as exc:
        return _reject(DUPLICATE_KEY, "duplicate key %r" % exc.key)
    except ValueError as exc:
        return _reject(INVALID_JSON, str(exc))
    if not isinstance(obj, dict):
        return _reject(NOT_AN_OBJECT, "top-level %s" % type(obj).__name__)
    try:
        normalizer = NORMALIZERS[norm_version]
    except KeyError:
        raise ValueError("no normalizer registered for version %r"
                         % (norm_version,))
    return normalizer(obj)


def _parse_v1_amount(value):
    """Return a Decimal amount, or a NormResult reject."""
    if not isinstance(value, str):
        return _reject(INVALID_FIELD, "amount must be a decimal string")
    if not _AMOUNT_RE.match(value):
        return _reject(INVALID_FIELD, "amount %r is not a decimal" % value)
    amount = Decimal(value)
    if -amount.as_tuple().exponent > 2:
        return _reject(AMOUNT_PRECISION,
                       "amount %r exceeds 2 fractional digits" % value)
    return amount.quantize(_CENT)


def _normalize(obj, allow_currency_default):
    version = obj.get("version", _MISSING)
    if version is _MISSING:
        return _reject(MISSING_FIELD, "version")
    if isinstance(version, bool) or not isinstance(version, int):
        return _reject(INVALID_FIELD, "version must be an integer")
    if version not in (1, 2):
        return _reject(UNKNOWN_VERSION, "version=%r" % (version,))

    rec_id = obj.get("id", _MISSING)
    if rec_id is _MISSING:
        return _reject(MISSING_FIELD, "id")
    if not isinstance(rec_id, str) or rec_id == "":
        return _reject(INVALID_FIELD, "id must be a non-empty string")

    currency = obj.get("currency", _MISSING)
    if currency is _MISSING:
        if not allow_currency_default:
            return _reject(MISSING_FIELD, "currency")
        currency = "CNY"
    if not isinstance(currency, str) or not _CURRENCY_RE.match(currency):
        return _reject(INVALID_FIELD, "currency must be a 3-letter code")

    if version == 1:
        if "amount" not in obj:
            return _reject(MISSING_FIELD, "amount")
        amount = _parse_v1_amount(obj["amount"])
        if isinstance(amount, NormResult):
            return amount
    else:
        if "amount_cents" not in obj:
            return _reject(MISSING_FIELD, "amount_cents")
        cents = obj["amount_cents"]
        if isinstance(cents, bool) or not isinstance(cents, int):
            return _reject(INVALID_FIELD, "amount_cents must be an integer")
        amount = (Decimal(cents) * _CENT).quantize(_CENT)

    if "note" not in obj:
        note_state, note = NOTE_MISSING, None
    elif obj["note"] is None:
        note_state, note = NOTE_NULL, None
    elif isinstance(obj["note"], str):
        note_state, note = NOTE_PRESENT, obj["note"]
    else:
        return _reject(INVALID_FIELD, "note must be a string or null")

    return NormResult(True, rec_id=rec_id, amount=amount, currency=currency,
                      note_state=note_state, note=note)


def _normalize_v1(obj):
    return _normalize(obj, allow_currency_default=False)


def _normalize_v2(obj):
    return _normalize(obj, allow_currency_default=True)


NORMALIZERS = {
    1: _normalize_v1,
    2: _normalize_v2,
}
