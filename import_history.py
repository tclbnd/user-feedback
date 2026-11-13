"""Deterministic legacy Parquet directory importer.

Files written by different schema generations are aligned by the Arrow field
ID metadata (``PARQUET:field_id``), recursively.  Column names are never used
to decide identity; they only appear in diagnostics.  Legacy files without
field IDs may supply an explicit ``<stem>.ids.json`` sidecar mapping dotted
field paths to IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Target contract
# ---------------------------------------------------------------------------

TARGET_FIELD_NAMES = {
    1: "id",
    2: "title",
    3: "score",
    4: "items",
    5: "name",
    6: "amount",
    7: "utc",
}

TARGET_IDS = frozenset(TARGET_FIELD_NAMES)

# Fixed-point target: decimal(6, 2) == [-9999.99, 9999.99], ROUND_HALF_EVEN.
DECIMAL_QUANTUM = Decimal("0.01")
DECIMAL_MAX = Decimal("9999.99")
DECIMAL_CONTEXT_PRECISION = 120

# Supported instants span years 0001..9999 so every accepted value renders as
# a four digit ISO 8601 year.  Values outside this convention are rejected.
MIN_INSTANT_NS = -62_135_596_800 * 1_000_000_000
MAX_INSTANT_NS = 253_402_300_799 * 1_000_000_000 + 999_999_999

TIMESTAMP_UNIT_NS = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

ID_METADATA_KEYS = (b"PARQUET:field_id", b"field_id")
SYSTEM_FIELD_PREFIXES = ("__index_level_",)

_NS_PER_SECOND = 1_000_000_000
_SECONDS_PER_DAY = 86_400


# ---------------------------------------------------------------------------
# Schema walking
# ---------------------------------------------------------------------------


@dataclass
class FieldNode:
    """One physically present Arrow field, including structural wrappers."""

    path: str
    alias_path: str
    root_name: str
    arrow_field: pa.Field
    wrapper: bool = False
    parent_path: Optional[str] = None
    embedded_id: Optional[int] = None
    effective_id: Optional[int] = None
    invalid_id_reason: Optional[str] = None


@dataclass(frozen=True)
class SchemaIssue:
    code: str
    field: Optional[str]
    detail: str


class IssueList:
    """Ordered, de-duplicated collection of schema issues."""

    def __init__(self) -> None:
        self._items: List[SchemaIssue] = []

    def add(self, code: str, field: Optional[str], detail: str = "") -> None:
        issue = SchemaIssue(code, field, detail)
        if issue not in self._items:
            self._items.append(issue)

    def __iter__(self) -> Iterable[SchemaIssue]:
        return iter(self.sorted())

    def __bool__(self) -> bool:
        return bool(self._items)

    def sorted(self) -> List[SchemaIssue]:
        return sorted(
            self._items,
            key=lambda i: (i.field is None, i.field or "", i.code, i.detail),
        )


def _raw_field_id(arrow_field: pa.Field) -> Tuple[Optional[int], Optional[str]]:
    metadata = arrow_field.metadata or {}
    raw: Optional[bytes] = None
    for key in ID_METADATA_KEYS:
        if key in metadata:
            raw = metadata[key]
            break
    if raw is None:
        return None, None
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None, "non-UTF-8 field ID metadata"
    try:
        value = int(text)
    except ValueError:
        return None, "non-integer field ID {!r}".format(text)
    if value < 0:
        return None, "negative field ID {}".format(value)
    return value, None


def _is_system_field(arrow_field: pa.Field) -> bool:
    return any(arrow_field.name.startswith(prefix) for prefix in SYSTEM_FIELD_PREFIXES)


def _is_list_type(field_type: pa.DataType) -> bool:
    return bool(
        pa.types.is_list(field_type)
        or pa.types.is_large_list(field_type)
        or pa.types.is_fixed_size_list(field_type)
    )


def _is_string_type(field_type: pa.DataType) -> bool:
    if pa.types.is_string(field_type) or pa.types.is_large_string(field_type):
        return True
    if pa.types.is_dictionary(field_type):
        return _is_string_type(field_type.value_type)
    return False


def _is_integer_type(field_type: pa.DataType) -> bool:
    return bool(
        pa.types.is_int8(field_type)
        or pa.types.is_int16(field_type)
        or pa.types.is_int32(field_type)
        or pa.types.is_int64(field_type)
        or pa.types.is_uint8(field_type)
        or pa.types.is_uint16(field_type)
        or pa.types.is_uint32(field_type)
        or pa.types.is_uint64(field_type)
    )


def _walk_field(
    arrow_field: pa.Field,
    path: str,
    alias_path: str,
    root_name: str,
    parent_path: Optional[str],
    wrapper: bool,
) -> Iterable[FieldNode]:
    embedded_id, invalid = _raw_field_id(arrow_field)
    node = FieldNode(
        path=path,
        alias_path=alias_path,
        root_name=root_name,
        arrow_field=arrow_field,
        wrapper=wrapper,
        parent_path=parent_path,
        embedded_id=embedded_id,
        effective_id=embedded_id,
        invalid_id_reason=invalid,
    )
    yield node

    field_type = arrow_field.type
    if pa.types.is_struct(field_type):
        for child in field_type:
            if _is_system_field(child):
                continue
            child_path = path + "." + child.name
            child_alias = alias_path + "." + child.name
            yield from _walk_field(
                child, child_path, child_alias, root_name, path, False
            )
    elif _is_list_type(field_type):
        value_field = field_type.value_field
        value_path = path + "." + value_field.name
        # Dotted sidecar paths may omit the physical "element" wrapper, so the
        # wrapper and its scalar element share the parent alias.
        yield from _walk_field(
            value_field, value_path, alias_path, root_name, path, True
        )


def _walk_schema(schema: pa.Schema) -> List[FieldNode]:
    nodes: List[FieldNode] = []
    for arrow_field in schema:
        if _is_system_field(arrow_field):
            continue
        nodes.extend(
            _walk_field(
                arrow_field,
                arrow_field.name,
                arrow_field.name,
                arrow_field.name,
                None,
                False,
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Legacy ID sidecar
# ---------------------------------------------------------------------------


def _load_ids_sidecar(path: Path) -> Tuple[Optional[Dict[str, int]], IssueList]:
    issues = IssueList()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.add("IDS_JSON_UNREADABLE", None, str(exc))
        return None, issues
    except UnicodeDecodeError as exc:
        issues.add("IDS_JSON_UNREADABLE", None, str(exc))
        return None, issues

    duplicate_keys: List[str] = []

    def object_pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate_keys.append(key)
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=object_pairs_hook)
    except json.JSONDecodeError as exc:
        issues.add(
            "IDS_JSON_INVALID",
            None,
            "JSON parse error at line {} column {}: {}".format(exc.lineno, exc.colno, exc.msg),
        )
        return None, issues

    for key in sorted(set(duplicate_keys)):
        issues.add("IDS_JSON_DUPLICATE_KEY", key, "path {!r} mapped more than once".format(key))

    if not isinstance(data, dict):
        issues.add("IDS_JSON_INVALID", None, "top-level value is not an object")
        return None, issues

    mapping: Dict[str, int] = {}
    for raw_path in sorted(data.keys(), key=str):
        raw_id = data[raw_path]
        if not isinstance(raw_path, str) or not raw_path:
            issues.add("IDS_JSON_INVALID", None, "invalid path {!r}".format(raw_path))
            continue
        if isinstance(raw_id, bool):
            issues.add("IDS_JSON_INVALID", raw_path, "boolean is not a field ID")
            continue
        if isinstance(raw_id, int):
            value = raw_id
        elif isinstance(raw_id, str):
            try:
                value = int(raw_id.strip())
            except ValueError:
                issues.add(
                    "IDS_JSON_INVALID",
                    raw_path,
                    "non-integer field ID {!r}".format(raw_id),
                )
                continue
        else:
            issues.add("IDS_JSON_INVALID", raw_path, "field ID must be an integer")
            continue
        if value < 0:
            issues.add("IDS_JSON_INVALID", raw_path, "negative field ID {}".format(value))
            continue
        mapping[raw_path] = value
    return mapping, issues


# ---------------------------------------------------------------------------
# Per-file schema plan
# ---------------------------------------------------------------------------


@dataclass
class ItemRefs:
    node: FieldNode
    is_list: bool
    element_is_struct: bool
    element_path: Optional[str]
    scalar_name_node: Optional[FieldNode]
    name_node: Optional[FieldNode]
    amount_node: Optional[FieldNode]


@dataclass
class FilePlan:
    path: Path
    table: pa.Table
    nodes: List[FieldNode]
    issues: IssueList
    refs: Dict[str, Any]
    id_source: str
    schema_summary: Dict[str, Any]
    sidecar: Optional[Path] = None


def _canonical_schema_summary(nodes: Sequence[FieldNode]) -> Dict[str, Any]:
    fields: List[Dict[str, Any]] = []
    for node in nodes:
        fields.append(
            {
                "path": node.path,
                "id": node.effective_id,
                "type": str(node.arrow_field.type),
                "nullable": bool(node.arrow_field.nullable),
            }
        )
    fields.sort(
        key=lambda entry: (
            entry["path"],
            entry["id"] is None,
            entry["id"] if entry["id"] is not None else -1,
            entry["type"],
            entry["nullable"],
        )
    )
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "fields": fields,
    }


def _apply_sidecar(
    nodes: Sequence[FieldNode],
    mapping: Dict[str, int],
    issues: IssueList,
) -> bool:
    """Apply a path -> ID map and report structural ambiguity."""

    by_path: Dict[str, List[FieldNode]] = defaultdict(list)
    alias_by_path: Dict[str, List[FieldNode]] = defaultdict(list)
    for node in nodes:
        by_path[node.path].append(node)
        # The implicit list-element wrapper shares its parent's alias; it is a
        # structural node, not a data column, so it never competes for a
        # sidecar key (it can still be addressed by its physical path).
        if node.alias_path != node.path and not node.wrapper:
            alias_by_path[node.alias_path].append(node)

    applied = False
    for key in sorted(mapping):
        value = mapping[key]
        exact = by_path.get(key, [])
        aliases = [
            node
            for node in alias_by_path.get(key, [])
            if node not in exact
        ]

        if not exact and not aliases:
            issues.add(
                "MAPPING_PATH_NOT_FOUND",
                key,
                "sidecar path does not correspond to any physical column",
            )
            continue
        if len(exact) > 1:
            # Two physical columns produce the same dotted path, e.g. a literal
            # column named "a.b" next to struct "a" with child "b".
            issues.add(
                "MAPPING_AMBIGUOUS_PATH",
                key,
                "path is produced by {} distinct physical columns".format(len(exact)),
            )
            continue
        if exact and aliases:
            # Example: a column literally named "a.b" plus list "a" with a
            # scalar element "b".  The key cannot say which one is meant.
            issues.add(
                "MAPPING_AMBIGUOUS_PATH",
                key,
                "path matches both {} and {}".format(
                    exact[0].path, ",".join(sorted(node.path for node in aliases))
                ),
            )
            continue
        if len(aliases) > 1:
            issues.add(
                "MAPPING_AMBIGUOUS_PATH",
                key,
                "path matches several columns: {}".format(
                    ",".join(sorted(node.path for node in aliases))
                ),
            )
            continue
        target = exact[0] if exact else aliases[0]

        if target.invalid_id_reason is not None:
            # Invalid embedded metadata is already a file-level failure, the
            # sidecar cannot silently repair it.
            issues.add(
                "MAPPING_CONFLICT",
                key,
                "column has invalid embedded field ID ({}) and sidecar says {}".format(
                    target.invalid_id_reason, value
                ),
            )
            continue
        if target.embedded_id is not None and target.embedded_id != value:
            issues.add(
                "MAPPING_CONFLICT",
                key,
                "embedded ID {} disagrees with sidecar ID {}".format(
                    target.embedded_id, value
                ),
            )
            continue
        if target.effective_id is None:
            target.effective_id = value
            applied = True
        elif target.effective_id != value:
            issues.add(
                "MAPPING_CONFLICT",
                key,
                "column already aligned to ID {} but sidecar says {}".format(
                    target.effective_id, value
                ),
            )
            continue
    return applied


def _resolve_items(
    node: FieldNode,
    nodes: Sequence[FieldNode],
) -> ItemRefs:
    field_type = node.arrow_field.type
    if not _is_list_type(field_type):
        return ItemRefs(node, False, False, None, None, None, None)

    value_field = field_type.value_field
    element_path = node.path + "." + value_field.name
    is_struct = pa.types.is_struct(value_field.type)

    name_node: Optional[FieldNode] = None
    amount_node: Optional[FieldNode] = None
    scalar_name_node: Optional[FieldNode] = None

    if is_struct:
        for child in nodes:
            if child.parent_path != element_path:
                continue
            if child.effective_id == 5:
                name_node = child
            elif child.effective_id == 6:
                amount_node = child
    else:
        for child in nodes:
            if child.path == element_path and child.effective_id == 5:
                scalar_name_node = child
                break

    return ItemRefs(
        node=node,
        is_list=True,
        element_is_struct=is_struct,
        element_path=element_path,
        scalar_name_node=scalar_name_node,
        name_node=name_node,
        amount_node=amount_node,
    )


def _items_shape_problem(refs: ItemRefs) -> Optional[str]:
    """Explain why ID 4's element shape cannot be projected, if it cannot."""

    if not refs.is_list:
        return "ID 4 must be a list, found {}".format(refs.node.arrow_field.type)

    value_type = refs.node.arrow_field.type.value_field.type
    if refs.element_is_struct:
        return None
    if _is_list_type(value_type):
        return (
            "ID 4 element is itself a list; the target only models a struct "
            "element with IDs 5/6 or a scalar element carrying ID 5"
        )
    if refs.scalar_name_node is None:
        return (
            "ID 4 element {} carries no field ID 5, so it cannot be projected "
            "as items.name".format(value_type)
        )
    if not _is_string_type(value_type):
        return "ID 4 element with ID 5 must be string, found {}".format(value_type)
    return None


def _build_plan(path: Path) -> FilePlan:
    issues = IssueList()
    try:
        table = pq.read_table(path)
    except Exception as exc:  # pragma: no cover - corrupt input
        empty = _canonical_schema_summary([])
        issues.add("READ_ERROR", None, "{}: {}".format(type(exc).__name__, exc))
        return FilePlan(path, pa.table({}), [], issues, {}, "none", empty)

    nodes = _walk_schema(table.schema)

    for node in nodes:
        if node.invalid_id_reason is not None:
            issues.add("INVALID_FIELD_ID", node.path, node.invalid_id_reason)

    missing_ids = any(
        node.effective_id is None and not node.wrapper for node in nodes
    )

    sidecar_path: Optional[Path] = None
    mapping: Optional[Dict[str, int]] = None
    used_sidecar = False
    if missing_ids:
        candidate = path.with_suffix(".ids.json")
        if candidate.exists():
            sidecar_path = candidate
            mapping, sidecar_issues = _load_ids_sidecar(candidate)
            for issue in sidecar_issues.sorted():
                issues.add(issue.code, issue.field, issue.detail)
            if mapping is not None:
                used_sidecar = _apply_sidecar(nodes, mapping, issues)

    for node in nodes:
        if node.wrapper or node.effective_id is not None:
            continue
        if node.invalid_id_reason is not None:
            continue
        issues.add(
            "NO_ID",
            node.path,
            "column has no field ID and no unambiguous sidecar mapping",
        )

    # A logical target ID may occur only once per file; duplicates make
    # alignment ambiguous.  Unrecognized IDs are ignored, so duplicates of
    # those are not a problem.
    id_to_paths: Dict[int, List[str]] = defaultdict(list)
    for node in nodes:
        if node.wrapper or node.effective_id is None:
            continue
        if node.effective_id not in TARGET_IDS:
            continue
        id_to_paths[node.effective_id].append(node.path)
    for field_id in sorted(id_to_paths):
        paths = id_to_paths[field_id]
        if len(paths) < 2:
            continue
        ordered = sorted(paths)
        for path_text in ordered:
            others = ",".join(p for p in ordered if p != path_text)
            issues.add(
                "DUPLICATE_FIELD_ID",
                path_text,
                "ID {} is also carried by {}".format(field_id, others),
            )

    top_nodes = [node for node in nodes if node.parent_path is None and not node.wrapper]
    top_by_id: Dict[int, FieldNode] = {}
    for node in top_nodes:
        if node.effective_id is None:
            continue
        if node.effective_id in TARGET_IDS:
            top_by_id.setdefault(node.effective_id, node)

    refs: Dict[str, Any] = {}
    for field_id, key in ((1, "id"), (2, "title"), (3, "score"), (7, "utc")):
        node = top_by_id.get(field_id)
        if node is not None:
            refs[key] = node
    node4 = top_by_id.get(4)
    if node4 is not None:
        item_refs = _resolve_items(node4, nodes)
        refs["items"] = item_refs
        shape_problem = _items_shape_problem(item_refs)
        if shape_problem is not None:
            # The element shape is a schema property, not a per-row value
            # problem: report the file once instead of rejecting every row.
            issues.add("UNSUPPORTED_ITEMS_SHAPE", node4.path, shape_problem)

    # Target IDs 1/2/3/4/7 belong at the top level and 5/6 inside the element
    # of list 4.  A target ID anywhere else cannot be aligned safely, so the
    # file is reported rather than silently dropping or mis-joining it.
    allowed: set = set()
    for ref in refs.values():
        if isinstance(ref, ItemRefs):
            allowed.add(id(ref.node))
            for item_node in (ref.scalar_name_node, ref.name_node, ref.amount_node):
                if item_node is not None:
                    allowed.add(id(item_node))
        else:
            allowed.add(id(ref))
    for node in nodes:
        if node.wrapper or node.effective_id is None:
            continue
        if node.effective_id not in TARGET_IDS or id(node) in allowed:
            continue
        if len(id_to_paths.get(node.effective_id, ())) > 1:
            # Already reported as DUPLICATE_FIELD_ID; do not pile on.
            continue
        expected = (
            "inside the element of list ID 4"
            if node.effective_id in (5, 6)
            else "at the top level"
        )
        issues.add(
            "UNEXPECTED_ID_PLACEMENT",
            node.path,
            "ID {} must appear {}, not under {!r}".format(
                node.effective_id, expected, node.parent_path
            ),
        )

    if issues:
        id_source = "none"
    elif used_sidecar:
        id_source = "ids_json"
    else:
        id_source = "embedded"

    return FilePlan(
        path=path,
        table=table,
        nodes=nodes,
        issues=issues,
        refs=refs,
        id_source=id_source,
        schema_summary=_canonical_schema_summary(nodes),
        sidecar=sidecar_path,
    )


# ---------------------------------------------------------------------------
# Value conversion
# ---------------------------------------------------------------------------


def _format_decimal(
    value: Any, field_type: pa.DataType
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (formatted, reason, detail).

    The target is decimal(6, 2).  Source values are quantized with
    ROUND_HALF_EVEN; values whose magnitude exceeds 9999.99 *after* rounding
    are reported as DECIMAL_OVERFLOW, which makes the scale expansion (more
    source fraction digits -> fewer) and the precision collapse explicit.
    """

    if value is None:
        return None, None, None
    if pa.types.is_decimal(field_type):
        if not isinstance(value, Decimal):
            try:
                value = Decimal(value)
            except (InvalidOperation, TypeError, ValueError):
                return None, "TYPE_MISMATCH", "value is not a decimal"
    elif _is_integer_type(field_type):
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "TYPE_MISMATCH", "value is not an integer"
        value = Decimal(value)
    else:
        return (
            None,
            "TYPE_MISMATCH",
            "unsupported decimal source type {}".format(field_type),
        )

    exact = value
    try:
        with localcontext() as context:
            context.prec = DECIMAL_CONTEXT_PRECISION
            quantized = exact.quantize(DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        return (
            None,
            "DECIMAL_OVERFLOW",
            "{} cannot be quantized to 2 decimal places".format(exact),
        )

    if quantized > DECIMAL_MAX or quantized < -DECIMAL_MAX:
        return (
            None,
            "DECIMAL_OVERFLOW",
            "{} rounds to {} outside [-9999.99, 9999.99]".format(exact, quantized),
        )
    if quantized == 0:
        quantized = Decimal("0.00")
    return format(quantized, "f"), None, None


def _timestamp_bounds(unit: str) -> Tuple[int, int]:
    factor = TIMESTAMP_UNIT_NS[unit]
    lower = -((-MIN_INSTANT_NS) // factor)
    upper = MAX_INSTANT_NS // factor
    return max(lower, INT64_MIN), min(upper, INT64_MAX)


def _format_instant_ns(ns: int) -> str:
    seconds, nanos = divmod(ns, _NS_PER_SECOND)
    days, second_of_day = divmod(seconds, _SECONDS_PER_DAY)
    date = datetime(1970, 1, 1) + timedelta(days=days)
    hour, remainder = divmod(second_of_day, 3_600)
    minute, second = divmod(remainder, 60)
    text = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}".format(
        date.year, date.month, date.day, hour, minute, second
    )
    if nanos:
        text += "." + "{:09d}".format(nanos).rstrip("0")
    return text + "Z"


def _format_timestamp(raw: int, unit: str) -> Tuple[Optional[str], Optional[str]]:
    lower, upper = _timestamp_bounds(unit)
    if lower <= raw <= upper:
        return _format_instant_ns(raw * TIMESTAMP_UNIT_NS[unit]), None

    declared_factor = TIMESTAMP_UNIT_NS[unit]
    candidates = [
        (candidate, TIMESTAMP_UNIT_NS[candidate])
        for candidate in TIMESTAMP_UNIT_NS
        if TIMESTAMP_UNIT_NS[candidate] < declared_factor
    ]
    # Prefer the coarsest finer unit that makes the value fit, so the report is
    # deterministic and maximally plausible.
    for candidate, factor in sorted(candidates, key=lambda item: -item[1]):
        candidate_lower, candidate_upper = _timestamp_bounds(candidate)
        if candidate_lower <= raw <= candidate_upper:
            return None, (
                "value fits {} not declared {}; unit label is inconsistent".format(
                    candidate, unit
                )
            )
    return None, (
        "value {} is outside the supported 0001..9999 instant range for unit {}".format(
            raw, unit
        )
    )


# ---------------------------------------------------------------------------
# Row import
# ---------------------------------------------------------------------------


@dataclass
class AcceptedRow:
    row: Dict[str, Any]
    file_name: str
    row_number: int


def _make_reject(
    plan: FilePlan,
    row_number: Optional[int],
    field_name: Optional[str],
    reason: str,
    source: Optional[str],
    detail: Optional[str],
) -> Dict[str, Any]:
    return {
        "file": plan.path.name,
        "row": row_number,
        "field": field_name,
        "reason": reason,
        "source": source,
        "detail": detail,
    }


def _root_column(table: pa.Table, node: FieldNode) -> pa.ChunkedArray:
    index = table.schema.get_field_index(node.root_name)
    if index < 0:
        raise KeyError(
            "column {!r} is not in the physical schema".format(node.root_name)
        )
    return table.column(index)


def _extract_values(table: pa.Table, node: FieldNode) -> List[Any]:
    column = _root_column(table, node)
    field_type = node.arrow_field.type
    if pa.types.is_timestamp(field_type):
        # Raw integer epochs: to_pylist() raises for ns values that cannot be
        # represented as datetime, and timezone/semantics checks happen below.
        raw = column.combine_chunks().cast(pa.int64())
        return raw.to_pylist()
    return column.to_pylist()


def _list_offsets(column: pa.Array) -> List[int]:
    field_type = column.type
    if pa.types.is_fixed_size_list(field_type):
        size = field_type.list_size
        return [index * size for index in range(len(column) + 1)]
    return column.offsets.to_pylist()


def _extract_items(
    table: pa.Table, refs: ItemRefs
) -> Tuple[List[Any], Optional[str]]:
    """Read the items column without materializing unrelated child columns.

    Reading struct children directly avoids converting columns that are not
    part of the target contract; pyarrow's ``to_pylist`` raises for some
    decodable-but-unrepresentable values (for example out-of-datetime-range
    nanosecond timestamps) even when those columns would be ignored.

    Each row becomes ``None`` or a list of element dictionaries with the keys
    ``null``, ``name`` and ``amount`` already projected by field ID.
    """

    column = _root_column(table, refs.node).combine_chunks()
    field_type = refs.node.arrow_field.type
    if not _is_list_type(field_type):
        return [], "items must be a list, found {}".format(field_type)

    offsets = _list_offsets(column)
    values = column.values
    row_nulls = column.is_null().to_pylist()

    if refs.element_is_struct:
        if not pa.types.is_struct(values.type):
            return [], "list element is not a struct"
        element_nulls = values.is_null().to_pylist()

        def struct_field_values(node: Optional[FieldNode]) -> Optional[List[Any]]:
            if node is None:
                return None
            index = values.type.get_field_index(node.arrow_field.name)
            if index < 0:
                return None
            return values.field(index).to_pylist()

        name_values = struct_field_values(refs.name_node)
        amount_values = struct_field_values(refs.amount_node)
    else:
        element_nulls = values.is_null().to_pylist()
        # A scalar element can serve as the name only when its own field ID is
        # 5 (recorded on the list's value field).
        if refs.scalar_name_node is not None:
            name_values = values.to_pylist()
        else:
            name_values = None
        amount_values = None

    rows: List[Any] = []
    for row_index in range(len(column)):
        if row_nulls[row_index]:
            rows.append(None)
            continue
        elements: List[Dict[str, Any]] = []
        for position in range(offsets[row_index], offsets[row_index + 1]):
            elements.append(
                {
                    "null": bool(element_nulls[position]),
                    "name": None if name_values is None else name_values[position],
                    "amount": (
                        None if amount_values is None else amount_values[position]
                    ),
                }
            )
        rows.append(elements)
    return rows, None


def _convert_items(
    refs: ItemRefs,
    value: List[Dict[str, Any]],
    problems: List[Tuple[str, str, Optional[str], str]],
) -> List[Any]:
    converted: List[Any] = []
    for index, element in enumerate(value):
        field_name = "items[{}]".format(index)
        if element["null"]:
            converted.append(None)
            continue

        if not refs.element_is_struct and refs.scalar_name_node is None:
            problems.append(
                (
                    field_name,
                    "TYPE_MISMATCH",
                    refs.element_path,
                    "list element is not a struct and its field ID is not 5",
                )
            )
            converted.append(None)
            continue

        item: Dict[str, Any] = {"name": None, "amount": None}

        name_node = refs.name_node if refs.element_is_struct else refs.scalar_name_node
        name_value = element["name"]
        if name_node is not None and name_value is not None:
            if not _is_string_type(name_node.arrow_field.type):
                problems.append(
                    (
                        field_name + ".name",
                        "TYPE_MISMATCH",
                        name_node.path,
                        "name must be string, found {}".format(
                            name_node.arrow_field.type
                        ),
                    )
                )
            else:
                item["name"] = name_value

        amount_node = refs.amount_node
        if amount_node is not None:
            formatted, reason, detail = _format_decimal(
                element["amount"], amount_node.arrow_field.type
            )
            if reason is not None:
                problems.append(
                    (field_name + ".amount", reason, amount_node.path, detail)
                )
            else:
                item["amount"] = formatted

        converted.append(item)
    return converted


def _import_file(plan: FilePlan) -> Tuple[List[AcceptedRow], List[Dict[str, Any]]]:
    table = plan.table
    readers: Dict[str, Tuple[FieldNode, List[Any]]] = {}
    for key in ("id", "title", "score", "utc"):
        node = plan.refs.get(key)
        if node is None:
            continue
        try:
            readers[key] = (node, _extract_values(table, node))
        except Exception as exc:  # pragma: no cover - unreadable column
            reject = _make_reject(
                plan,
                None,
                key,
                "AMBIGUOUS_SCHEMA",
                node.path,
                "unreadable source column: {}: {}".format(type(exc).__name__, exc),
            )
            return [], [reject]

    items_values: List[Any] = []
    item_refs = plan.refs.get("items")
    if item_refs is not None:
        # _build_plan already rejected unsupported element shapes, so the
        # remaining cases are a plain list we can read positionally.
        try:
            items_values, items_problem = _extract_items(table, item_refs)
        except Exception as exc:  # pragma: no cover - unreadable column
            items_problem = "unreadable source column: {}: {}".format(
                type(exc).__name__, exc
            )
        if items_problem is not None:
            reject = _make_reject(
                plan,
                None,
                "items",
                "TYPE_MISMATCH",
                item_refs.node.path,
                items_problem,
            )
            return [], [reject]

    accepted: List[AcceptedRow] = []
    rejects: List[Dict[str, Any]] = []

    for row_index in range(table.num_rows):
        row_number = row_index + 1
        row: Dict[str, Any] = {}
        problems: List[Tuple[str, str, Optional[str], str]] = []

        # ---- id ---------------------------------------------------------
        if "id" not in plan.refs:
            row["id"] = None
        else:
            node, values = readers["id"]
            value = values[row_index]
            row["id"] = None
            if value is not None:
                if _is_string_type(node.arrow_field.type):
                    row["id"] = value
                else:
                    problems.append(
                        (
                            "id",
                            "TYPE_MISMATCH",
                            node.path,
                            "id must be string, found {}".format(node.arrow_field.type),
                        )
                    )

        # ---- title ------------------------------------------------------
        if "title" not in plan.refs:
            row["title"] = "untitled"
        else:
            node, values = readers["title"]
            value = values[row_index]
            row["title"] = None
            if value is not None:
                if _is_string_type(node.arrow_field.type):
                    row["title"] = value
                else:
                    problems.append(
                        (
                            "title",
                            "TYPE_MISMATCH",
                            node.path,
                            "title must be string, found {}".format(node.arrow_field.type),
                        )
                    )

        # ---- score ------------------------------------------------------
        if "score" not in plan.refs:
            row["score"] = None
        else:
            node, values = readers["score"]
            formatted, reason, detail = _format_decimal(
                values[row_index], node.arrow_field.type
            )
            if reason is None:
                row["score"] = formatted
            else:
                row["score"] = None
                problems.append(("score", reason, node.path, detail))

        # ---- items ------------------------------------------------------
        if item_refs is None:
            row["items"] = None
        else:
            value = items_values[row_index]
            if value is None:
                row["items"] = None
            else:
                # An empty list stays [], element-level problems reject the row.
                row["items"] = _convert_items(item_refs, value, problems)

        # ---- utc --------------------------------------------------------
        if "utc" not in plan.refs:
            row["utc"] = None
        else:
            node, values = readers["utc"]
            value = values[row_index]
            field_type = node.arrow_field.type
            row["utc"] = None
            if value is None:
                pass
            elif not pa.types.is_timestamp(field_type):
                problems.append(
                    (
                        "utc",
                        "TIMESTAMP_SEMANTICS",
                        node.path,
                        "utc must be a timestamp, found {}".format(field_type),
                    )
                )
            elif field_type.tz is None:
                problems.append(
                    (
                        "utc",
                        "TIMESTAMP_SEMANTICS",
                        node.path,
                        "naive timestamp has no timezone; UTC semantics are undefined",
                    )
                )
            elif field_type.unit not in TIMESTAMP_UNIT_NS:
                problems.append(
                    (
                        "utc",
                        "TIMESTAMP_SEMANTICS",
                        node.path,
                        "unsupported timestamp unit {!r}".format(field_type.unit),
                    )
                )
            else:
                formatted, problem = _format_timestamp(int(value), field_type.unit)
                if problem is None:
                    row["utc"] = formatted
                else:
                    problems.append(("utc", "TIMESTAMP_SEMANTICS", node.path, problem))

        if problems:
            for field_name, reason, source, detail in problems:
                rejects.append(
                    _make_reject(plan, row_number, field_name, reason, source, detail)
                )
            continue

        accepted.append(AcceptedRow(row, plan.path.name, row_number))

    return accepted, rejects


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _collect_name_evidence(
    plans: Sequence[FilePlan],
) -> Tuple[Dict[str, List[int]], Dict[str, List[str]]]:
    """Map leaf column name -> IDs and -> paths seen across the directory.

    This is only ever used to *explain* an ambiguous schema.  It is never used
    to resolve an identity: a name that was renamed and later re-used for a new
    ID must not be allowed to merge two logical columns.
    """

    name_ids: Dict[str, set] = defaultdict(set)
    name_paths: Dict[str, set] = defaultdict(set)
    for plan in plans:
        for node in plan.nodes:
            if node.wrapper or node.effective_id is None:
                continue
            name_ids[node.arrow_field.name].add(node.effective_id)
            name_paths[node.arrow_field.name].add(node.path)
    return (
        {name: sorted(ids) for name, ids in name_ids.items()},
        {name: sorted(paths) for name, paths in name_paths.items()},
    )


def _name_hint(
    column_name: str,
    name_ids: Dict[str, List[int]],
    name_paths: Dict[str, List[str]],
) -> Optional[str]:
    ids = name_ids.get(column_name, [])
    if not ids:
        return None
    target_ids = sorted(field_id for field_id in ids if field_id in TARGET_IDS)
    other_ids = sorted(field_id for field_id in ids if field_id not in TARGET_IDS)
    pieces: List[str] = []
    if target_ids:
        pieces.append(
            "name is used for target ID(s) {} elsewhere".format(
                ",".join(str(value) for value in target_ids)
            )
        )
    if other_ids:
        pieces.append(
            "name is also used for non-target ID(s) {} elsewhere".format(
                ",".join(str(value) for value in other_ids)
            )
        )
    if len(ids) > 1:
        pieces.append(
            "name was re-used across IDs {} so a name-based join would merge them".format(
                ",".join(str(value) for value in ids)
            )
        )
    if len(name_paths.get(column_name, [])) > 1:
        pieces.append(
            "name occurs at paths {}".format(",".join(name_paths[column_name]))
        )
    return "; ".join(pieces) if pieces else None


def import_directory(input_dir: Path) -> Dict[str, Any]:
    files = sorted(input_dir.glob("*.parquet"), key=lambda path: path.name)

    accepted: List[AcceptedRow] = []
    rejects: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    schema_versions: Dict[str, Dict[str, Any]] = {}
    plans: List[Tuple[FilePlan, Path]] = []

    for path in files:
        plan = _build_plan(path)
        plans.append((plan, path))

    name_ids, name_paths = _collect_name_evidence([plan for plan, _ in plans])

    for plan, path in plans:
        if plan.issues:
            for issue in plan.issues:
                detail = issue.code
                if issue.detail:
                    detail += ": " + issue.detail
                reject = _make_reject(
                    plan,
                    None,
                    issue.field,
                    "AMBIGUOUS_SCHEMA",
                    issue.field,
                    detail,
                )
                # Explain *why* the name cannot be trusted: across the whole
                # input the same name may map to several IDs (rename followed
                # by re-use), which is exactly what makes a name-based import
                # merge unrelated columns.
                hint = None
                if issue.field is not None:
                    leaf = issue.field.rsplit(".", 1)[-1]
                    if leaf != "element":
                        hint = _name_hint(leaf, name_ids, name_paths)
                if hint:
                    reject["hint"] = hint
                rejects.append(reject)
        else:
            file_rows, file_rejects = _import_file(plan)
            accepted.extend(file_rows)
            rejects.extend(file_rejects)

        digest = plan.schema_summary["digest"]
        version = schema_versions.setdefault(
            digest, {"digest": digest, "fields": plan.schema_summary["fields"], "files": []}
        )
        version["files"].append(path.name)

        source: Dict[str, Any] = {
            "file": path.name,
            "file_sha256": _file_digest(path),
            "id_source": plan.id_source,
            "schema_digest": digest,
            "schema": plan.schema_summary,
        }
        if plan.sidecar is not None:
            source["ids_file"] = plan.sidecar.name
        sources.append(source)

    accepted.sort(
        key=lambda accepted_row: (
            accepted_row.row["id"] is None,
            "" if accepted_row.row["id"] is None else accepted_row.row["id"],
            accepted_row.file_name,
            accepted_row.row_number,
        )
    )
    rejects.sort(
        key=lambda reject: (
            reject["file"],
            0 if reject["row"] is None else reject["row"],
            reject["field"] or "",
            reject["reason"],
            reject["source"] or "",
            reject["detail"] or "",
        )
    )

    return {
        "rows": [accepted_row.row for accepted_row in accepted],
        "rejects": rejects,
        "sources": sources,
        "manifest": {
            "input_files": [source["file"] for source in sources],
            "schema_versions": [
                schema_versions[digest] for digest in sorted(schema_versions)
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import legacy Parquet partitions by field ID."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = import_directory(args.input)
    args.output.write_text(
        json.dumps(result, default=str, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
