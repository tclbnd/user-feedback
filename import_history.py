"""Import historical Parquet partitions by stable field IDs.

The importer never trusts physical field names across schema generations.
Arrow/Parquet field IDs (or an explicit side-car mapping for old files) form
the recursive source-to-target alignment.  Values are normalized into one
logical schema, while records that cannot be converted safely are isolated
with field-level diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


MISSING = object()

AMBIGUOUS_SCHEMA = "AMBIGUOUS_SCHEMA"
DECIMAL_OVERFLOW = "DECIMAL_OVERFLOW"
MISSING_FIELD = "MISSING_FIELD"
TIMESTAMP_SEMANTICS = "TIMESTAMP_SEMANTICS"
TYPE_MISMATCH = "TYPE_MISMATCH"
UNREADABLE_FILE = "UNREADABLE_FILE"

TARGET_DECIMAL_PRECISION = 6
TARGET_DECIMAL_SCALE = 2
TARGET_DECIMAL_QUANTUM = Decimal("0.01")
TARGET_DECIMAL_MAX = Decimal("9999.99")
TIMESTAMP_UNITS = {
    "s": 1,
    "ms": 1_000,
    "us": 1_000_000,
    "ns": 1_000_000_000,
}


@dataclass(frozen=True)
class TargetField:
    """Declarative target schema node."""

    field_id: int
    name: str
    kind: str
    default: Any = MISSING
    required: bool = False
    precision: Optional[int] = None
    scale: Optional[int] = None
    children: Optional[Dict[int, "TargetField"]] = None
    item: Optional["TargetField"] = None


@dataclass
class SourceField:
    """Resolved source schema node, keyed by stable field ID where possible."""

    field_id: Optional[int]
    name: str
    path: str
    depth: int
    arrow_type: pa.DataType
    nullable: bool
    children: Dict[int, "SourceField"] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaBuild:
    fields_by_id: Dict[int, SourceField]
    descriptors: List[Dict[str, Any]]
    rejects: List[Dict[str, Any]]
    schema_version: str
    embedded_id_count: int
    sidecar_id_count: int


def make_reject(
    file_name: str,
    row: Optional[int],
    field_path: Optional[str],
    reason: str,
    source: Any = MISSING,
    message: Any = MISSING,
) -> Dict[str, Any]:
    reject = {
        "file": file_name,
        "row": row,
        "field": field_path,
        "reason": reason,
    }
    if source is not MISSING:
        reject["source"] = source
    if message is not MISSING:
        reject["message"] = message
    return reject


def target_schema() -> Dict[int, TargetField]:
    item_struct = TargetField(
        field_id=0,
        name="",
        kind="struct",
        children={
            5: TargetField(5, "name", "string"),
            6: TargetField(
                6,
                "amount",
                "decimal",
                precision=TARGET_DECIMAL_PRECISION,
                scale=TARGET_DECIMAL_SCALE,
            ),
        },
    )
    return {
        1: TargetField(1, "id", "string", required=True),
        2: TargetField(2, "title", "string", default="untitled"),
        3: TargetField(
            3,
            "score",
            "decimal",
            precision=TARGET_DECIMAL_PRECISION,
            scale=TARGET_DECIMAL_SCALE,
        ),
        4: TargetField(4, "items", "list", item=item_struct),
        7: TargetField(7, "utc", "timestamp"),
    }


TARGET_FIELDS = target_schema()


def is_list_like(data_type: pa.DataType) -> bool:
    return (
        pa.types.is_list(data_type)
        or pa.types.is_large_list(data_type)
        or pa.types.is_fixed_size_list(data_type)
    )


def is_system_field(name: str) -> bool:
    """Ignore Pandas/Parquet bookkeeping columns such as __index_level_0__."""

    return len(name) >= 4 and name.startswith("__") and name.endswith("__")


def embedded_field_id(field: pa.Field) -> Optional[int]:
    raw = (field.metadata or {}).get(b"PARQUET:field_id")
    if raw is None:
        return None
    try:
        return int(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def sidecar_for(parquet_file: Path) -> Path:
    return parquet_file.with_name(f"{parquet_file.stem}.ids.json")


def load_sidecar(parquet_file: Path) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    sidecar = sidecar_for(parquet_file)
    if not sidecar.exists():
        return {}, []

    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [
            make_reject(
                parquet_file.name,
                None,
                "$",
                AMBIGUOUS_SCHEMA,
                sidecar.name,
                f"invalid ID mapping: {exc}",
            )
        ]

    if not isinstance(payload, dict):
        return {}, [
            make_reject(
                parquet_file.name,
                None,
                "$",
                AMBIGUOUS_SCHEMA,
                sidecar.name,
                "ID mapping must be a JSON object",
            )
        ]

    mapping: Dict[str, int] = {}
    rejects: List[Dict[str, Any]] = []
    for key, value in payload.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            rejects.append(
                make_reject(
                    parquet_file.name,
                    None,
                    key if isinstance(key, str) else "$",
                    AMBIGUOUS_SCHEMA,
                    sidecar.name,
                    "mapping value must be a non-negative integer field ID",
                )
            )
        else:
            mapping[key] = value
    return mapping, rejects


def describe_source(field: SourceField) -> Dict[str, Any]:
    descriptor = {
        "path": field.path,
        "name": field.name,
        "field_id": field.field_id,
        "type": str(field.arrow_type),
        "nullable": field.nullable,
    }
    child_descriptors = [
        describe_source(child)
        for child in sorted(field.children.values(), key=lambda item: item.path)
    ]
    if child_descriptors:
        descriptor["children"] = child_descriptors
    return descriptor


def build_source_schema(
    arrow_schema: pa.Schema,
    sidecar: Dict[str, int],
    parquet_file: Path,
) -> SchemaBuild:
    file_name = parquet_file.name
    sidecar_name = sidecar_for(parquet_file).name
    fields_by_id: Dict[int, SourceField] = {}
    rejects: List[Dict[str, Any]] = []
    used_sidecar_paths = set()
    embedded_id_count = 0
    sidecar_id_count = 0

    def resolve_id(arrow_field: pa.Field, path: str) -> Optional[int]:
        nonlocal embedded_id_count, sidecar_id_count
        resolved = embedded_field_id(arrow_field)
        if resolved is not None:
            embedded_id_count += 1

        # Exact path takes precedence over dot splitting, so a literal top
        # level column name containing a dot can still be mapped explicitly.
        if path in sidecar:
            used_sidecar_paths.add(path)
            mapped_id = sidecar[path]
            sidecar_id_count += 1
            if resolved is not None and resolved != mapped_id:
                rejects.append(
                    make_reject(
                        file_name,
                        None,
                        path,
                        AMBIGUOUS_SCHEMA,
                        path,
                        (
                            f"embedded field ID {resolved} conflicts with "
                            f"side-car ID {mapped_id} from {sidecar_name}"
                        ),
                    )
                )
            elif resolved is None:
                resolved = mapped_id

        if resolved is None and not is_system_field(arrow_field.name):
            rejects.append(
                make_reject(
                    file_name,
                    None,
                    path,
                    AMBIGUOUS_SCHEMA,
                    path,
                    "field has no ID and no deterministic side-car mapping",
                )
            )
        return resolved

    def register_id(source_field: SourceField) -> None:
        if source_field.field_id is None:
            return
        previous = fields_by_id.get(source_field.field_id)
        if previous is not None:
            rejects.append(
                make_reject(
                    file_name,
                    None,
                    source_field.path,
                    AMBIGUOUS_SCHEMA,
                    source_field.path,
                    (
                        f"duplicate field ID {source_field.field_id}; "
                        f"also used by {previous.path}"
                    ),
                )
            )
        else:
            fields_by_id[source_field.field_id] = source_field

    def child_fields(arrow_field: pa.Field) -> List[pa.Field]:
        if pa.types.is_struct(arrow_field.type):
            return list(arrow_field.type)
        if is_list_like(arrow_field.type):
            value_field = arrow_field.type.value_field
            if pa.types.is_struct(value_field.type):
                return list(value_field.type)
        return []
    def walk(arrow_field: pa.Field, path: str, depth: int) -> SourceField:
        field_id = resolve_id(arrow_field, path)
        source_field = SourceField(
            field_id=field_id,
            name=arrow_field.name,
            path=path,
            depth=depth,
            arrow_type=arrow_field.type,
            nullable=arrow_field.nullable,
        )
        register_id(source_field)

        seen_names = set()
        for child in child_fields(arrow_field):
            child_path = f"{path}.{child.name}"
            if child.name in seen_names:
                rejects.append(
                    make_reject(
                        file_name,
                        None,
                        child_path,
                        AMBIGUOUS_SCHEMA,
                        child_path,
                        "duplicate physical field name makes name-based access ambiguous",
                    )
                )
            seen_names.add(child.name)
            child_source = walk(child, child_path, depth + 1)
            if child_source.field_id is not None:
                source_field.children[child_source.field_id] = child_source
        return source_field

    top_fields: List[SourceField] = []
    seen_top_names = set()
    for arrow_field in arrow_schema:
        if arrow_field.name in seen_top_names:
            rejects.append(
                make_reject(
                    file_name,
                    None,
                    arrow_field.name,
                    AMBIGUOUS_SCHEMA,
                    arrow_field.name,
                    "duplicate physical top-level field name",
                )
            )
        seen_top_names.add(arrow_field.name)
        top_fields.append(walk(arrow_field, arrow_field.name, 0))

    for key in sorted(set(sidecar) - used_sidecar_paths):
        rejects.append(
            make_reject(
                file_name,
                None,
                key,
                AMBIGUOUS_SCHEMA,
                sidecar_name,
                "side-car mapping does not match a field path in this schema",
            )
        )

    descriptors = [describe_source(item) for item in top_fields]
    canonical = json.dumps(
        descriptors,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    schema_version = (
        "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    return SchemaBuild(
        fields_by_id=fields_by_id,
        descriptors=descriptors,
        rejects=rejects,
        schema_version=schema_version,
        embedded_id_count=embedded_id_count,
        sidecar_id_count=sidecar_id_count,
    )


def valid_timestamp_source(source: SourceField) -> bool:
    return (
        pa.types.is_timestamp(source.arrow_type)
        and source.arrow_type.tz is not None
        and source.arrow_type.unit in TIMESTAMP_UNITS
    )


def prepare_timestamp_values(
    table: pa.Table, fields_by_id: Dict[int, SourceField]
) -> Dict[str, Tuple[List[Optional[int]], str]]:
    source = fields_by_id.get(7)
    if source is None or source.depth != 0 or not valid_timestamp_source(source):
        return {}

    unit = source.arrow_type.unit
    try:
        normalized = pc.cast(
            table.column(source.name), pa.timestamp(unit, tz="UTC")
        )
        epoch_values = pc.cast(normalized, pa.int64(), safe=False).to_pylist()
    except pa.ArrowInvalid:
        return {}
    return {source.path: (epoch_values, unit)}


def format_timestamp(epoch_value: int, unit: str) -> str:
    factor = TIMESTAMP_UNITS[unit]
    # divmod keeps the fractional component non-negative for negative epochs.
    seconds, fraction = divmod(int(epoch_value), factor)
    moment = datetime.fromtimestamp(seconds, timezone.utc)
    base = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if fraction == 0:
        return f"{base}Z"
    width = len(str(factor)) - 1
    fractional = str(fraction).zfill(width)
    return f"{base}.{fractional}Z"


def add_conversion_error(
    errors: List[Dict[str, Any]],
    target_path: str,
    source_path: Optional[str],
    reason: str,
    message: Optional[str] = None,
) -> None:
    error = {
        "field": target_path,
        "reason": reason,
    }
    if source_path is not None:
        error["source"] = source_path
    if message is not None:
        error["message"] = message
    errors.append(error)


def normalize_decimal(
    value: Any,
    spec: TargetField,
    target_path: str,
    source_path: str,
    errors: List[Dict[str, Any]],
) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        add_conversion_error(
            errors,
            target_path,
            source_path,
            TYPE_MISMATCH,
            f"cannot convert {type(value).__name__} to decimal",
        )
        return None

    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite():
            raise InvalidOperation("non-finite decimal")
        quantized = decimal_value.quantize(
            TARGET_DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    except (InvalidOperation, ValueError):
        add_conversion_error(
            errors, target_path, source_path, DECIMAL_OVERFLOW
        )
        return None

    if quantized < -TARGET_DECIMAL_MAX or quantized > TARGET_DECIMAL_MAX:
        add_conversion_error(
            errors,
            target_path,
            source_path,
            DECIMAL_OVERFLOW,
            f"value is outside [-{TARGET_DECIMAL_MAX}, {TARGET_DECIMAL_MAX}]",
        )
        return None
    return f"{quantized:.{TARGET_DECIMAL_SCALE}f}"


def convert_struct(
    spec: TargetField,
    source: Optional[SourceField],
    value: Any,
    target_path: str,
    source_path: Optional[str],
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
    list_source: Optional[SourceField] = None,
) -> Optional[Dict[str, Any]]:
    if source is not None and list_source is None and not pa.types.is_struct(
        source.arrow_type
    ):
        add_conversion_error(
            errors, target_path, source_path, TYPE_MISMATCH, "expected struct"
        )
        return None
    if not isinstance(value, dict):
        add_conversion_error(
            errors, target_path, source_path, TYPE_MISMATCH, "expected record"
        )
        return None

    result: Dict[str, Any] = {}
    assert spec.children is not None
    for child_id in sorted(spec.children):
        child_spec = spec.children[child_id]
        child_source = source.children.get(child_id) if source else None
        child_target = (
            f"{target_path}.{child_spec.name}"
            if target_path
            else child_spec.name
        )
        child_source_path = (
            f"{source_path}.{child_source.name}"
            if source is not None and child_source is not None and source_path
            else None
        )
        child_value = value.get(child_source.name) if child_source else MISSING
        result[child_spec.name] = convert_field(
            child_spec,
            child_source,
            child_value,
            child_target,
            child_source_path,
            errors,
            context,
        )
    return result


def convert_field(
    spec: TargetField,
    source: Optional[SourceField],
    value: Any,
    target_path: str,
    source_path: Optional[str],
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> Any:
    if source is None:
        if spec.default is not MISSING:
            return spec.default
        if spec.required:
            add_conversion_error(
                errors, target_path, None, MISSING_FIELD, "required field is absent"
            )
        return None

    if spec.kind == "timestamp" and not valid_timestamp_source(source):
        add_conversion_error(
            errors,
            target_path,
            source_path,
            TIMESTAMP_SEMANTICS,
            "timestamp must be s/ms/us/ns and carry a time zone",
        )
        return None

    if value is None:
        # This is an explicit null.  A missing column is handled above and can
        # therefore receive the field default instead.
        return None

    if spec.kind == "string":
        if not isinstance(value, str):
            add_conversion_error(
                errors, target_path, source_path, TYPE_MISMATCH, "expected string"
            )
            return None
        return value

    if spec.kind == "decimal":
        return normalize_decimal(
            value, spec, target_path, source_path or source.path, errors
        )

    if spec.kind == "timestamp":
        prepared = context.get("timestamps", {}).get(source.path)
        if prepared is None:
            add_conversion_error(
                errors, target_path, source_path, TIMESTAMP_SEMANTICS
            )
            return None
        epoch_values, unit = prepared
        epoch_value = epoch_values[context["row_number"]]
        if epoch_value is None:
            return None
        try:
            return format_timestamp(epoch_value, unit)
        except (OverflowError, OSError, ValueError):
            add_conversion_error(
                errors,
                target_path,
                source_path,
                TIMESTAMP_SEMANTICS,
                "timestamp value cannot be represented as UTC ISO 8601",
            )
            return None

    if spec.kind == "list":
        if not is_list_like(source.arrow_type):
            add_conversion_error(
                errors, target_path, source_path, TYPE_MISMATCH, "expected list"
            )
            return None
        if not isinstance(value, list):
            add_conversion_error(
                errors, target_path, source_path, TYPE_MISMATCH, "expected list"
            )
            return None

        assert spec.item is not None
        value_field = source.arrow_type.value_field
        result: List[Any] = []
        for index, element in enumerate(value):
            item_target = f"{target_path}[{index}]"
            item_source = (
                f"{source_path}[{index}]" if source_path is not None else None
            )
            if element is None:
                result.append(None)
                continue
            if not pa.types.is_struct(value_field.type) or not isinstance(
                element, dict
            ):
                add_conversion_error(
                    errors,
                    item_target,
                    item_source,
                    TYPE_MISMATCH,
                    "expected list element to be a record",
                )
                continue
            result.append(
                convert_struct(
                    spec.item,
                    source,
                    element,
                    item_target,
                    item_source,
                    errors,
                    context,
                    list_source=source,
                )
            )
        return result

    if spec.kind == "struct":
        return convert_struct(
            spec, source, value, target_path, source_path, errors, context
        )

    add_conversion_error(errors, target_path, source_path, TYPE_MISMATCH)
    return None


def source_manifest_entry(
    parquet_file: Path,
    build: Optional[SchemaBuild],
    sidecar_exists: bool,
    physical_rows: Optional[int],
    accepted_rows: int,
    rejected_rows: Optional[int],
) -> Dict[str, Any]:
    if build is None:
        mapping = "none"
        version = None
        fields: List[Dict[str, Any]] = []
    else:
        if build.embedded_id_count and build.sidecar_id_count:
            mapping = "mixed"
        elif build.sidecar_id_count:
            mapping = "sidecar"
        elif build.embedded_id_count:
            mapping = "field_id"
        else:
            mapping = "unresolved"
        version = build.schema_version
        fields = build.descriptors

    return {
        "file": parquet_file.name,
        "schema_version": version,
        "id_mapping": mapping,
        "sidecar": sidecar_for(parquet_file).name if sidecar_exists else None,
        "physical_rows": physical_rows,
        "accepted_rows": accepted_rows,
        "rejected_rows": rejected_rows,
        "fields": fields,
    }


def import_file(
    parquet_file: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    sidecar, sidecar_rejects = load_sidecar(parquet_file)
    try:
        table = pq.read_table(parquet_file)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not stop the batch
        reject = make_reject(
            parquet_file.name,
            None,
            None,
            UNREADABLE_FILE,
            str(parquet_file),
            str(exc),
        )
        return (
            [],
            [reject],
            source_manifest_entry(
                parquet_file,
                None,
                sidecar_for(parquet_file).exists(),
                None,
                0,
                None,
            ),
        )

    build = build_source_schema(table.schema, sidecar, parquet_file)
    file_rejects = sidecar_rejects + build.rejects
    if file_rejects:
        return (
            [],
            file_rejects,
            source_manifest_entry(
                parquet_file,
                build,
                sidecar_for(parquet_file).exists(),
                table.num_rows,
                0,
                table.num_rows,
            ),
        )

    timestamp_values = prepare_timestamp_values(table, build.fields_by_id)
    rows = table.to_pylist()
    accepted_rows: List[Dict[str, Any]] = []
    rejects: List[Dict[str, Any]] = []

    for row_number, row in enumerate(rows, start=1):
        record: Dict[str, Any] = {}
        errors: List[Dict[str, Any]] = []
        context = {
            "row_number": row_number - 1,
            "timestamps": timestamp_values,
        }

        for field_id in sorted(TARGET_FIELDS):
            spec = TARGET_FIELDS[field_id]
            source = build.fields_by_id.get(field_id)
            if source is not None and source.depth != 0:
                source = None
            source_path = source.path if source is not None else None
            value = row.get(source.name) if source is not None else MISSING
            record[spec.name] = convert_field(
                spec,
                source,
                value,
                spec.name,
                source_path,
                errors,
                context,
            )

        if errors:
            for error in errors:
                rejects.append(
                    make_reject(
                        parquet_file.name,
                        row_number,
                        error["field"],
                        error["reason"],
                        error.get("source", MISSING),
                        error.get("message", MISSING),
                    )
                )
        else:
            accepted_rows.append(record)

    rejected_row_count = len({item["row"] for item in rejects})
    manifest = source_manifest_entry(
        parquet_file,
        build,
        sidecar_for(parquet_file).exists(),
        table.num_rows,
        len(accepted_rows),
        rejected_row_count,
    )
    return accepted_rows, rejects, manifest


def reject_sort_key(reject: Dict[str, Any]) -> Tuple[str, int, str, str]:
    return (
        reject["file"],
        -1 if reject["row"] is None else reject["row"],
        reject["field"] or "",
        json.dumps(reject, ensure_ascii=False, sort_keys=True),
    )


def import_files(files: Sequence[Path]) -> Dict[str, Any]:
    ordered_files = sorted(files, key=lambda item: item.name)
    rows: List[Dict[str, Any]] = []
    rejects: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []

    for parquet_file in ordered_files:
        file_rows, file_rejects, source = import_file(parquet_file)
        rows.extend(file_rows)
        rejects.extend(file_rejects)
        sources.append(source)

    rows.sort(key=lambda row: row["id"])
    rejects.sort(key=reject_sort_key)
    return {"rows": rows, "rejects": rejects, "sources": sources}


def import_path(input_dir: Path) -> Dict[str, Any]:
    return import_files(list(Path(input_dir).glob("*.parquet")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = import_path(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
