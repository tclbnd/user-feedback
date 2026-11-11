"""Versioned record normalization with explicit, documented rules.

Business rules (the "explicit rules" of the pipeline)
------------------------------------------------------
Two payload versions coexist in the same source:

* ``version=1`` -- amount is a decimal **string** in ``amount``;
* ``version=2`` -- amount is an integer number of cents in ``amount_cents``.

Both normalize to an exact :class:`decimal.Decimal` amount with exactly two
fractional digits plus a currency. Money never passes through a binary float.

Handling of the ambiguous cases:

==============================  ==============================================
Situation                       Rule
==============================  ==============================================
Duplicate JSON key (anywhere    reject ``duplicate_key``; the first duplicated
in the payload)                 key name is reported in ``detail``
Missing required field          reject ``missing_field`` with the field name
Required field explicitly null  reject ``null_field`` with the field name
Wrong field type                reject ``bad_type`` (booleans are never ints)
Unknown version                 reject ``unknown_version``
v1 amount not decimal-text      reject ``amount_format``
v1 amount with > 2 decimals     reject ``amount_precision`` (no silent rounding)
amount outside the declared     reject ``amount_range``
precision/range bounds
currency not 3 uppercase ASCII  reject ``currency_format``
letters
``note`` missing vs. null       kept distinct: ``note_present`` is False only
                                when the key is absent; an explicit null is
                                present with a null value
Unknown/extra fields            ignored, so newer field formats can be mixed
                                into a batch without changing old rows
==============================  ==============================================

Every normalizer is pinned to a schema *version*. A historical record is
always re-normalized by the normalizer of its own version, so introducing a
newer normalizer can never change the meaning of rows already stored.
"""

import json
import re
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from .decoder import RawLine, decode_payload

# Declared business precision: at most 10 integer digits and 2 fraction digits.
MAX_AMOUNT = Decimal("9999999999.99")
CENT_QUANTUM = Decimal("0.01")
_AMOUNT_TEXT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

COMMON_REQUIRED = ("id", "version", "currency")


class Reject(Exception):
    """Structured rejection of one source line.

    ``reason`` is a stable machine code, ``field`` is the offending field
    (or ``None`` for whole-document errors) and ``detail`` is human-readable
    context. The triple is persisted as JSON in the rejection table.
    """

    def __init__(self, reason: str, field: Optional[str] = None, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.field = field
        self.detail = detail

    def to_struct(self) -> Dict[str, Optional[str]]:
        return {"reason": self.reason, "field": self.field, "detail": self.detail}


class Normalized:
    """Normalized record ready for persistence (exact decimal money)."""

    __slots__ = (
        "record_id",
        "version",
        "currency",
        "amount",
        "amount_cents",
        "note_present",
        "note_value",
        "normalized_by",
    )

    def __init__(
        self,
        record_id: str,
        version: int,
        currency: str,
        amount: Decimal,
        amount_cents: int,
        note_present: bool,
        note_value: Optional[str],
        normalized_by: int,
    ):
        self.record_id = record_id
        self.version = version
        self.currency = currency
        self.amount = amount  # always quantized to 0.01
        self.amount_cents = amount_cents
        self.note_present = note_present
        self.note_value = note_value
        self.normalized_by = normalized_by

    def as_tuple(self) -> Tuple:
        """Content identity tuple, used by differential comparisons."""
        return (
            self.record_id,
            self.version,
            self.currency,
            str(self.amount),
            self.amount_cents,
            self.note_present,
            self.note_value,
            self.normalized_by,
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Normalized({self.as_tuple()!r})"


def _parse_json(raw: bytes) -> Any:
    """Parse one line, mapping encoding/syntax/duplicate errors to Reject."""
    try:
        text = decode_payload(raw)
    except UnicodeDecodeError as exc:
        raise Reject("encoding_error", None, f"invalid utf-8 at byte {exc.start}")

    duplicated = []

    def _hook(pairs):
        seen = set()
        for key, _value in pairs:
            if key in seen and key not in duplicated:
                duplicated.append(key)
            seen.add(key)
        return dict(pairs)

    try:
        value = json.loads(text, object_pairs_hook=_hook)
    except json.JSONDecodeError as exc:
        raise Reject("json_syntax_error", None, f"line {exc.lineno} col {exc.colno}: {exc.msg}")
    if duplicated:
        raise Reject("duplicate_key", duplicated[0], f"duplicate key {duplicated[0]!r}")
    return value


def _require_field(obj: Dict[str, Any], field: str, expected_type: Optional[type] = None) -> Any:
    if field not in obj:
        raise Reject("missing_field", field, f"required field {field!r} is absent")
    value = obj[field]
    if value is None:
        raise Reject("null_field", field, f"required field {field!r} is null")
    if expected_type is not None and (
        not isinstance(value, expected_type) or (expected_type is int and isinstance(value, bool))
    ):
        raise Reject("bad_type", field, f"field {field!r} has type {type(value).__name__}")
    return value


def _note(obj: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if "note" not in obj:
        return False, None  # distinct from an explicit null
    value = obj["note"]
    if value is None:
        return True, None
    if not isinstance(value, str):
        raise Reject("bad_type", "note", f"field 'note' has type {type(value).__name__}")
    return True, value


def _common(obj: Dict[str, Any]) -> Tuple[str, int, str, Tuple[bool, Optional[str]]]:
    record_id = _require_field(obj, "id", str)
    if not record_id:
        raise Reject("bad_type", "id", "field 'id' is an empty string")
    version = _require_field(obj, "version", int)
    # The accepted versions are exactly those with a pinned normalizer.
    # Registering a future version widens this gate without touching the
    # normalizers of historical versions.
    if version not in _NORMALIZERS:
        raise Reject("unknown_version", "version", f"unsupported version {version}")
    currency = _require_field(obj, "currency", str)
    if not _CURRENCY_RE.match(currency):
        raise Reject("currency_format", "currency", f"currency {currency!r} is not 3 uppercase letters")
    return record_id, version, currency, _note(obj)


def _normalize_v1(obj: Dict[str, Any]) -> Tuple[Decimal, int]:
    amount_text = _require_field(obj, "amount", str)
    if not _AMOUNT_TEXT_RE.match(amount_text):
        raise Reject("amount_format", "amount", f"amount {amount_text!r} is not plain decimal text")
    # The regex above admits only optional sign + digits + optional fraction,
    # so Decimal() cannot raise here; precision/range are validated explicitly.
    amount = Decimal(amount_text)
    if -amount.as_tuple().exponent > 2:
        raise Reject("amount_precision", "amount", f"amount {amount_text!r} has more than 2 decimal places")
    if abs(amount) > MAX_AMOUNT:
        raise Reject("amount_range", "amount", f"amount {amount_text!r} exceeds {MAX_AMOUNT}")
    amount = amount.quantize(CENT_QUANTUM)  # pads zeros; never rounds (precision checked)
    return amount, int((amount * 100).to_integral_value())


def _normalize_v2(obj: Dict[str, Any]) -> Tuple[Decimal, int]:
    cents = _require_field(obj, "amount_cents", int)  # bool rejected explicitly
    if abs(cents) > int(MAX_AMOUNT * 100):
        raise Reject("amount_range", "amount_cents", f"amount_cents {cents} exceeds declared bounds")
    amount = (Decimal(cents) / Decimal(100)).quantize(CENT_QUANTUM)
    return amount, cents


# Registry of schema versions -> normalizer. Historical versions stay pinned
# forever: adding version 3 below does not alter the output of versions 1/2.
_NORMALIZERS = {1: _normalize_v1, 2: _normalize_v2}


def normalize_line(raw_line: RawLine) -> Normalized:
    """Decode, validate and normalize one physical source line.

    Raises :class:`Reject` with a structured reason for every rule violation.
    """
    obj = _parse_json(raw_line.raw)
    if not isinstance(obj, dict):
        raise Reject("not_object", None, f"top-level value is {type(obj).__name__}")

    record_id, version, currency, (note_present, note_value) = _common(obj)
    amount, amount_cents = _NORMALIZERS[version](obj)

    return Normalized(
        record_id=record_id,
        version=version,
        currency=currency,
        amount=amount,
        amount_cents=amount_cents,
        note_present=note_present,
        note_value=note_value,
        normalized_by=version,
    )
