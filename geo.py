"""Versioned, hourly telemetry store with a spatial-grid range index.

Canonical geometry
------------------
A stored longitude is finite, lies in [-180, 180), and +180 is represented
as -180 because both spell the same meridian.  A query longitude interval
is the closed eastward arc from ``west`` to ``east``: it is represented as
one or two closed segments in the canonical domain.  ``west == -180 and
east == 180`` is the full circle, while equal normalized endpoints are a
single meridian.  Latitude and longitude-arc endpoints are closed; time
intervals are half-open ``[start, end)``.

Storage layout
--------------
``buckets/<sha256>.json`` holds one immutable, content-addressed bucket
payload.  ``versions/vN.json`` is the manifest for a published version: the
mapping from bucket key to payload hash plus the (device, t) key index.  It
is stored as a delta against the previous version, with periodic full
checkpoints to bound replay.  ``head.json`` names the live version.  An
apply writes only the buckets it touches, then its manifest, then atomically
replaces ``head.json``; the head replacement is the single commit point.
Readers therefore observe a complete old or a complete new version, and old
versions stay readable through ``query --version N``.
"""

import argparse
import bisect
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


CRS_NAME = "EPSG:4326"

LON_MIN = -180.0
LON_MAX = 180.0
LAT_MIN = -90.0
LAT_MAX = 90.0

# A 0.1 degree grid keeps cells small enough that queries over a sparse
# region touch few points, while manifests only track populated cells.
GRID_DEG = 0.1
LON_CELL_COUNT = int(round((LON_MAX - LON_MIN) / GRID_DEG))
LAT_CELL_COUNT = int(round((LAT_MAX - LAT_MIN) / GRID_DEG))
HOUR_SECONDS = 3600
INTERSECTION_EPS = 1e-9
# A version manifest is a delta against its predecessor; every so many
# versions a full checkpoint bounds the replay chain for old readers.
CHECKPOINT_INTERVAL = 16


class InputError(Exception):
    """A user-visible input error (exit status 2)."""


class _CrashBeforePublish(Exception):
    """Deliberate crash marker: exit 97 without publishing or cleanup."""


@dataclass(frozen=True)
class LongitudeArc:
    """Canonical representation of a closed eastward longitude arc."""

    full_circle: bool
    # Closed [lo, hi] segments in the canonical longitude domain.  The
    # upper end may be the sentinel 180, which no canonical point equals;
    # -180 is the canonical spelling of that same meridian.
    segments: Tuple[Tuple[float, float], ...]

    def contains(self, longitude: float) -> bool:
        if self.full_circle:
            return True
        lon = _canonical_lon(longitude)
        for lo, hi in self.segments:
            if lo <= lon <= hi:
                return True
        return False

    def query_segments(self) -> Tuple[Tuple[float, float], ...]:
        if self.full_circle:
            return ((LON_MIN, LON_MAX),)
        return self.segments


def _canonical_lon(value: float) -> float:
    # +180 and -180 denote one meridian.  Confining values to [-180, 180)
    # gives every longitude exactly one representation.
    if value == 180:
        return -180.0
    return float(value)


def _make_arc(west: float, east: float) -> LongitudeArc:
    # Recognize the full circle before normalizing +180: normalizing it
    # would turn -180..180 into -180..-180 and lose the wrap-around.
    if west == -180 and east == 180:
        return LongitudeArc(True, ())

    w = _canonical_lon(west)
    e = _canonical_lon(east)
    if w == e:
        return LongitudeArc(False, ((w, w),))
    if w < e:
        return LongitudeArc(False, ((w, e),))
    # Crossing the date line: [w, 180) union [-180, e].
    return LongitudeArc(False, ((w, 180.0), (-180.0, e)))


def _fmt_bound(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _validate_number(value: Any, location: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError("%s: expected a finite number" % location)
    number = float(value)
    if not math.isfinite(number):
        raise InputError("%s: expected a finite number" % location)
    if number < minimum or number > maximum:
        raise InputError(
            "%s: out of range [%s, %s]: %s"
            % (location, _fmt_bound(minimum), _fmt_bound(maximum), value)
        )
    return number


def _validate_integer(value: Any, location: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError("%s: expected an integer" % location)
    if positive and value <= 0:
        raise InputError("%s: expected a positive integer: %s" % (location, value))
    return value


def _require_field(obj: Dict[str, Any], field: str, location: str, expected: str) -> Any:
    if field not in obj:
        raise InputError("%s.%s: missing required %s" % (location, field, expected))
    return obj[field]


def _canonical_number(value: Any, integral: bool) -> Any:
    """Normalize a validated coordinate while retaining its JSON type."""
    if integral:
        return -180 if value == 180 else value
    if value == 180:
        return -180.0
    if value == 0:
        return 0.0  # collapse -0.0 so serialization is deterministic
    return float(value)


def _canonical_point(raw: Any, index: int) -> Dict[str, Any]:
    location = "points[%d]" % index
    if not isinstance(raw, dict):
        raise InputError("%s: expected an object" % location)

    device = _require_field(raw, "device", location, "device string")
    if not isinstance(device, str) or not device:
        raise InputError("%s.device: expected a non-empty string" % location)

    t = _validate_integer(
        _require_field(raw, "t", location, "Unix-second integer"),
        location + ".t",
    )
    rev = _validate_integer(
        _require_field(raw, "rev", location, "positive revision integer"),
        location + ".rev",
        positive=True,
    )

    lon_value = _require_field(raw, "lon", location, "longitude")
    lon = _validate_number(lon_value, location + ".lon", LON_MIN, LON_MAX)
    lat_value = _require_field(raw, "lat", location, "latitude")
    lat = _validate_number(lat_value, location + ".lat", LAT_MIN, LAT_MAX)

    source = _require_field(raw, "source", location, "source string")
    if not isinstance(source, str):
        raise InputError("%s.source: expected a string" % location)

    return {
        "device": device,
        "t": t,
        "rev": rev,
        "lon": _canonical_number(lon_value, isinstance(lon_value, int)),
        "lat": _canonical_number(lat_value, isinstance(lat_value, int)),
        "source": source,
    }


def _record_content(record: Dict[str, Any]) -> Tuple[Any, ...]:
    # device and t are the primary key and equal at the call site.  The
    # longitude is compared canonically, so 180 and -180 are equal.
    return (
        record["rev"],
        _canonical_lon(record["lon"]),
        float(record["lat"]),
        record["source"],
    )


def _hour_of(t: int) -> int:
    # Python // floors, including for negative Unix seconds.
    return t // HOUR_SECONDS


def _grid_index(value: float, origin: float, cell_size: float, count: int) -> int:
    # Floor placement guarantees the cell's closed bounds contain the value,
    # which is what the conservative bucket-intersection test relies on.
    # The clamp only handles the closed top end (+180 / +90).
    index = int(math.floor((value - origin) / cell_size))
    if index < 0:
        return 0
    if index >= count:
        return count - 1
    return index


def _lon_cell(lon: float) -> int:
    return _grid_index(_canonical_lon(lon), LON_MIN, GRID_DEG, LON_CELL_COUNT)


def _lat_cell(lat: float) -> int:
    return _grid_index(float(lat), LAT_MIN, GRID_DEG, LAT_CELL_COUNT)


def _bucket_key(record: Dict[str, Any]) -> Tuple[int, int, int]:
    return (_hour_of(record["t"]), _lon_cell(record["lon"]), _lat_cell(record["lat"]))


def _cell_bounds(origin: float, index: int, cell_size: float, maximum: float) -> Tuple[float, float]:
    lo = origin + index * cell_size
    hi = min(maximum, origin + (index + 1) * cell_size)
    return lo, hi


def _cell_range(lo: float, hi: float, origin: float, cell_size: float, count: int) -> Tuple[int, int]:
    """Inclusive cell-index range covering every cell that may hold [lo, hi].

    Both placement and this range use the same floor rounding, so any stored
    value v with lo <= v <= hi satisfies cell(lo) <= cell(v) <= cell(hi).
    """
    first = _grid_index(lo, origin, cell_size, count)
    last = _grid_index(hi, origin, cell_size, count)
    if last < first:
        first, last = last, first
    return first, last


def _bucket_key_text(key: Tuple[int, int, int]) -> str:
    return "%d/%d/%d" % key


def _parse_bucket_key(text: Any, location: str) -> Tuple[int, int, int]:
    if not isinstance(text, str):
        raise InputError("%s: expected a bucket key string" % location)
    parts = text.split("/")
    if len(parts) != 3:
        raise InputError("%s: malformed bucket key %r" % (location, text))
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        raise InputError("%s: malformed bucket key %r" % (location, text))


def _payload_text(records: Sequence[Dict[str, Any]]) -> str:
    return json.dumps(records, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def _validate_hash(value: Any, location: str) -> str:
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise InputError("%s: expected a sha256 content hash" % location)
    return value


class IndexVersion:
    """Fully resolved state of one published version.

    On disk, a version is either a full checkpoint or a delta against the
    previous version (see ``ManifestStore``).  This class only represents
    the resolved, complete bucket map and key index.
    """

    def __init__(
        self,
        version: int,
        total_points: int,
        buckets: Dict[Tuple[int, int, int], str],
        keys: Optional[Dict[str, str]] = None,
    ) -> None:
        self.version = version
        self.total_points = total_points
        self.buckets = buckets
        self.keys = keys if keys is not None else {}

    @classmethod
    def empty(cls) -> "IndexVersion":
        return cls(0, 0, {}, {})


class ManifestStore:
    """Reads and writes version manifests, delta-chained with checkpoints.

    A version file is either::

        {"version": N, "total_points": T, "full": true,
         "buckets": {...}, "keys": {...}}

    or a delta against version N-1::

        {"version": N, "total_points": T,
         "buckets_set": {...}, "buckets_del": [...],
         "keys_set": {...}, "keys_del": [...]}

    Old version files are never modified or deleted, so every published
    version stays independently readable.  Periodic full checkpoints bound
    the chain length, so resolving a version never walks the whole history.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, version: int) -> Path:
        return _manifest_path(self.root, version)

    def _read_raw(self, version: int) -> Dict[str, Any]:
        if version == 0:
            return {
                "version": 0,
                "total_points": 0,
                "full": True,
                "buckets": {},
                "keys": {},
            }
        path = self.path(version)
        if not path.exists():
            raise InputError("version %d: manifest not found" % version)
        data = _load_json_file(path, "version %d" % version)
        if not isinstance(data, dict):
            raise InputError("version %d: manifest must be a JSON object" % version)
        stored = _validate_integer(
            _require_field(data, "version", "version %d" % version, "version integer"),
            "version %d.version" % version,
        )
        if stored != version:
            raise InputError(
                "version %d: manifest declares version %d" % (version, stored)
            )
        return data

    def chain(self, version: int) -> List[Dict[str, Any]]:
        """Version files from the nearest checkpoint up to `version`."""
        chain = []
        current = version
        while True:
            data = self._read_raw(current)
            chain.append(data)
            if data.get("full") is True:
                break
            base = _validate_integer(
                _require_field(data, "base", "version %d" % current, "base version"),
                "version %d.base" % current,
            )
            if base >= current or base < 0:
                raise InputError(
                    "version %d: invalid base version %d" % (current, base)
                )
            current = base
        chain.reverse()
        return chain

    def chain_length(self, version: int) -> int:
        return sum(1 for data in self.chain(version) if data.get("full") is not True)

    def load(self, version: int) -> IndexVersion:
        if version == 0:
            return IndexVersion.empty()
        buckets: Dict[Tuple[int, int, int], str] = {}
        keys: Dict[str, str] = {}
        total = 0
        for data in self.chain(version):
            declared_version = data["version"]
            location = "version %d" % declared_version
            if data.get("full") is True:
                buckets = {}
                keys = {}
                for key_text, hash_value in _require_dict(data, "buckets", location).items():
                    buckets[_parse_bucket_key(key_text, location + ".buckets")] = _validate_hash(
                        hash_value, location + ".buckets[%r]" % key_text
                    )
                for key_text, bucket_text in _require_dict(data, "keys", location).items():
                    keys[key_text] = _validated_bucket_ref(bucket_text, buckets, location, key_text)
            else:
                for key_text in _require_list(data, "buckets_del", location):
                    buckets.pop(_parse_bucket_key(key_text, location + ".buckets_del"), None)
                for key_text in _require_list(data, "keys_del", location):
                    if not isinstance(key_text, str):
                        raise InputError("%s.keys_del: expected key strings" % location)
                    keys.pop(key_text, None)
                for key_text, hash_value in _require_dict(data, "buckets_set", location).items():
                    buckets[_parse_bucket_key(key_text, location + ".buckets_set")] = _validate_hash(
                        hash_value, location + ".buckets_set[%r]" % key_text
                    )
                for key_text, bucket_text in _require_dict(data, "keys_set", location).items():
                    keys[key_text] = _validated_bucket_ref(bucket_text, buckets, location, key_text)
            total = _validate_integer(
                _require_field(data, "total_points", location, "point count"),
                location + ".total_points",
            )
        if total < 0:
            raise InputError("version %d: total_points must be non-negative" % version)
        if len(keys) != total:
            raise InputError(
                "version %d: key index holds %d entries but total_points is %d"
                % (version, len(keys), total)
            )
        return IndexVersion(version, total, buckets, keys)

    # -- writing -----------------------------------------------------------

    def write_delta(self, state: IndexVersion, previous: IndexVersion) -> Dict[str, Any]:
        buckets_set: Dict[str, str] = {}
        for key, hash_value in state.buckets.items():
            if previous.buckets.get(key) != hash_value:
                buckets_set[_bucket_key_text(key)] = hash_value
        buckets_del = sorted(
            _bucket_key_text(key) for key in previous.buckets if key not in state.buckets
        )
        keys_set = {
            key_text: bucket_text
            for key_text, bucket_text in state.keys.items()
            if previous.keys.get(key_text) != bucket_text
        }
        keys_del = sorted(key_text for key_text in previous.keys if key_text not in state.keys)
        return {
            "version": state.version,
            "base": previous.version,
            "total_points": state.total_points,
            "buckets_set": dict(sorted(buckets_set.items())),
            "buckets_del": buckets_del,
            "keys_set": dict(sorted(keys_set.items())),
            "keys_del": keys_del,
        }

    def write_full(self, state: IndexVersion) -> Dict[str, Any]:
        return {
            "version": state.version,
            "total_points": state.total_points,
            "full": True,
            "buckets": {
                _bucket_key_text(key): state.buckets[key] for key in sorted(state.buckets)
            },
            "keys": dict(sorted(state.keys.items())),
        }


def _require_dict(obj: Dict[str, Any], field: str, location: str) -> Dict[str, Any]:
    value = _require_field(obj, field, location, "object")
    if not isinstance(value, dict):
        raise InputError("%s.%s: expected an object" % (location, field))
    return value


def _require_list(obj: Dict[str, Any], field: str, location: str) -> List[Any]:
    value = _require_field(obj, field, location, "list")
    if not isinstance(value, list):
        raise InputError("%s.%s: expected a list" % (location, field))
    return value


def _validated_bucket_ref(
    bucket_text: Any,
    buckets: Dict[Tuple[int, int, int], str],
    location: str,
    key_text: str,
) -> str:
    if not isinstance(bucket_text, str):
        raise InputError("%s.keys[%r]: expected a bucket key" % (location, key_text))
    bucket = _parse_bucket_key(bucket_text, "%s.keys[%r]" % (location, key_text))
    if bucket not in buckets:
        raise InputError(
            "%s.keys[%r]: bucket %r has no payload" % (location, key_text, bucket_text)
        )
    return bucket_text


class BucketReader:
    """Reads content-addressed bucket payloads, verifying hash and caching."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cache: Dict[str, List[Dict[str, Any]]] = {}

    def bucket_path(self, content_hash: str) -> Path:
        return self.root / "buckets" / ("%s.json" % content_hash)

    def read(self, content_hash: str) -> List[Dict[str, Any]]:
        cached = self.cache.get(content_hash)
        if cached is not None:
            return cached
        path = self.bucket_path(content_hash)
        data = _load_json_file(path, "bucket %s" % content_hash)
        if not isinstance(data, list):
            raise InputError("bucket %s: expected a list of records" % content_hash)
        records = [_canonical_point(raw, index) for index, raw in enumerate(data)]
        if _content_hash(_payload_text(records)) != content_hash:
            raise InputError(
                "bucket %s: payload does not match its content hash" % content_hash
            )
        self.cache[content_hash] = records
        return records

    def write(self, records: Sequence[Dict[str, Any]]) -> str:
        """Persist a bucket payload once and return its content hash."""
        text = _payload_text(records)
        content_hash = _content_hash(text)
        path = self.bucket_path(content_hash)
        if not path.exists():
            _atomic_write_text(path, text + "\n")
            self.cache[content_hash] = list(records)
        return content_hash


def _check_bucket_placement(bucket: Tuple[int, int, int], records: Sequence[Dict[str, Any]]) -> None:
    for record in records:
        actual = _bucket_key(record)
        if actual != bucket:
            raise InputError(
                "bucket %s: device %r at t=%d belongs in %s"
                % (_bucket_key_text(bucket), record["device"], record["t"], _bucket_key_text(actual))
            )


class ApplySession:
    """Copy-on-write view of a published version for one apply operation.

    Only buckets that actually change are serialized and written.  The
    previous version's manifest and bucket payloads are never modified, so
    an interrupted apply leaves the old version intact.
    """

    def __init__(self, root: Path, manifest: IndexVersion, reader: BucketReader) -> None:
        self.root = root
        self.base_version = manifest.version
        self.total_points = manifest.total_points
        self.reader = reader
        self.keys: Dict[str, str] = dict(manifest.keys)
        self.buckets: Dict[Tuple[int, int, int], str] = dict(manifest.buckets)
        # Only buckets listed here are read or rewritten.
        self.loaded: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
        self.touched: set = set()
        self.changed = False

    # -- bucket access -----------------------------------------------------

    def _bucket_records(self, bucket: Tuple[int, int, int]) -> List[Dict[str, Any]]:
        records = self.loaded.get(bucket)
        if records is None:
            content_hash = self.buckets.get(bucket)
            if content_hash is None:
                # Newly created bucket: there is no old payload to carry over.
                records = []
            else:
                records = list(self.reader.read(content_hash))
                _check_bucket_placement(bucket, records)
            self.loaded[bucket] = records
        return records

    def find(self, key_text: str) -> Tuple[Optional[Tuple[int, int, int]], Optional[Dict[str, Any]]]:
        bucket_text = self.keys.get(key_text)
        if bucket_text is None:
            return None, None
        bucket = _parse_bucket_key(bucket_text, "version keys")
        for record in self._bucket_records(bucket):
            if _key_text(record["device"], record["t"]) == key_text:
                return bucket, record
        # The key index and a bucket payload disagree: fail locatably rather
        # than silently insert a second row for the same primary key.
        raise InputError(
            "version keys: device %r at t=%d indexed under %s but absent from its bucket"
            % (json.loads(key_text)[0], json.loads(key_text)[1], bucket_text)
        )

    def insert(self, record: Dict[str, Any]) -> None:
        bucket = _bucket_key(record)
        self.touched.add(bucket)
        self._bucket_records(bucket).append(record)
        self.keys[_key_text(record["device"], record["t"])] = _bucket_key_text(bucket)
        self.total_points += 1
        self.changed = True

    def update(self, old_bucket: Tuple[int, int, int], record: Dict[str, Any]) -> None:
        new_bucket = _bucket_key(record)
        if new_bucket == old_bucket:
            rows = self._bucket_records(old_bucket)
            for index, existing in enumerate(rows):
                if existing["device"] == record["device"] and existing["t"] == record["t"]:
                    rows[index] = record
                    self.touched.add(old_bucket)
                    self.changed = True
                    return
            raise InputError("stored bucket is missing device %r at t=%d" % (record["device"], record["t"]))

        old_rows = self._bucket_records(old_bucket)
        for index, existing in enumerate(old_rows):
            if existing["device"] == record["device"] and existing["t"] == record["t"]:
                del old_rows[index]
                break
        else:
            raise InputError("stored bucket is missing device %r at t=%d" % (record["device"], record["t"]))
        self.touched.add(old_bucket)
        self.touched.add(new_bucket)
        self._bucket_records(new_bucket).append(record)
        self.keys[_key_text(record["device"], record["t"])] = _bucket_key_text(new_bucket)
        self.changed = True

    # -- publication -------------------------------------------------------

    def publish(self) -> IndexVersion:
        for bucket in sorted(self.touched):
            records = self.loaded.get(bucket, [])
            if records:
                self.buckets[bucket] = self.reader.write(records)
            else:
                self.buckets.pop(bucket, None)
        return IndexVersion(
            self.base_version + 1, self.total_points, dict(self.buckets), dict(self.keys)
        )


def _key_text(device: str, t: int) -> str:
    # Unambiguous because the device is JSON-escaped and the tuple is fixed.
    return json.dumps([device, t], ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Persistence


def _load_json_file(path: Path, description: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError("%s: cannot read %s: %s" % (description, path, exc))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            "%s: invalid JSON at line %d column %d: %s"
            % (description, exc.lineno, exc.colno, exc.msg)
        )


def _head_path(root: Path) -> Path:
    return root / "head.json"


def _manifest_path(root: Path, version: int) -> Path:
    return root / "versions" / ("v%d.json" % version)


def _read_head_version(root: Path) -> int:
    head = _head_path(root)
    if not head.exists():
        return 0
    data = _load_json_file(head, "head")
    if not isinstance(data, dict):
        raise InputError("head: expected a JSON object")
    return _validate_integer(
        _require_field(data, "version", "head", "version integer"),
        "head.version",
    )


def _load_version(root: Path, version: int) -> IndexVersion:
    if version == 0:
        return IndexVersion.empty()
    return ManifestStore(root).load(version)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(
        path, json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    )


class _ApplyLock:
    """Serializes writers; readers rely on atomic snapshot publication."""

    def __init__(self, root: Path) -> None:
        self.path = root / ".apply.lock"
        self.handle = None

    def __enter__(self) -> "_ApplyLock":
        self.handle = open(self.path, "a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


# ---------------------------------------------------------------------------
# apply


def _validate_apply_input(data: Any) -> List[Tuple[int, Dict[str, Any]]]:
    if not isinstance(data, dict):
        raise InputError("input: expected a JSON object")
    if "crs" not in data:
        raise InputError(
            'crs: missing required coordinate reference system (expected "%s")' % CRS_NAME
        )
    crs = data["crs"]
    if not isinstance(crs, str):
        raise InputError("crs: expected a string coordinate reference system")
    if crs != CRS_NAME:
        raise InputError(
            'crs: unsupported coordinate reference system "%s" (expected "%s")'
            % (crs, CRS_NAME)
        )
    points_raw = _require_field(data, "points", "input", "points list")
    if not isinstance(points_raw, list):
        raise InputError("points: expected a list")
    return [(index, _canonical_point(raw, index)) for index, raw in enumerate(points_raw)]


def apply(root: Path, input_path: Path, crash_before_publish: bool = False) -> Dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    incoming = _validate_apply_input(_load_json_file(input_path, "apply input"))

    with _ApplyLock(root):
        current_version = _read_head_version(root)
        manifest = _load_version(root, current_version)
        reader = BucketReader(root)
        session = ApplySession(root, manifest, reader)

        for index, record in incoming:
            key_text = _key_text(record["device"], record["t"])
            bucket, existing = session.find(key_text)
            if existing is None:
                session.insert(record)
                continue

            if record["rev"] < existing["rev"]:
                # An older revision has no effect even if its payload differs.
                continue
            if record["rev"] == existing["rev"]:
                if _record_content(record) == _record_content(existing):
                    # Identical duplicate: no new version, no touched bucket.
                    continue
                raise InputError(
                    "points[%d]: conflicting content for device %r at t=%d revision %d"
                    % (index, record["device"], record["t"], record["rev"])
                )
            if bucket is None:
                raise InputError(
                    "points[%d]: index lost device %r at t=%d"
                    % (index, record["device"], record["t"])
                )
            session.update(bucket, record)

        if not session.changed:
            return {"version": current_version, "total_points": session.total_points}

        if crash_before_publish:
            # Represent an interrupted update just before publication: no
            # published file is touched and no cleanup runs before exit 97.
            raise _CrashBeforePublish()

        # Bucket payloads are written before the manifest, and the manifest
        # before the head; each is atomic, and the head is the commit point.
        new_state = session.publish()
        store = ManifestStore(root)
        # The first published version is always a checkpoint; afterwards a
        # checkpoint appears whenever the delta chain grows long enough.
        if current_version == 0 or store.chain_length(current_version) + 1 >= CHECKPOINT_INTERVAL:
            record = store.write_full(new_state)
        else:
            record = store.write_delta(new_state, manifest)
        _atomic_write_json(_manifest_path(root, new_state.version), record)
        _atomic_write_json(_head_path(root), {"version": new_state.version})
        return {"version": new_state.version, "total_points": new_state.total_points}


# ---------------------------------------------------------------------------
# query


def _validate_query(raw: Any, index: int) -> Dict[str, Any]:
    location = "queries[%d]" % index
    if not isinstance(raw, dict):
        raise InputError("%s: expected an object" % location)
    query_id = _require_field(raw, "id", location, "id")
    start = _validate_integer(
        _require_field(raw, "start", location, "Unix-second integer"),
        location + ".start",
    )
    end = _validate_integer(
        _require_field(raw, "end", location, "Unix-second integer"),
        location + ".end",
    )
    west = _validate_number(
        _require_field(raw, "west", location, "longitude"), location + ".west", LON_MIN, LON_MAX
    )
    east = _validate_number(
        _require_field(raw, "east", location, "longitude"), location + ".east", LON_MIN, LON_MAX
    )
    south = _validate_number(
        _require_field(raw, "south", location, "latitude"), location + ".south", LAT_MIN, LAT_MAX
    )
    north = _validate_number(
        _require_field(raw, "north", location, "latitude"), location + ".north", LAT_MIN, LAT_MAX
    )
    return {
        "id": query_id,
        "west": west,
        "east": east,
        "south": south,
        "north": north,
        "start": start,
        "end": end,
    }


def _validate_query_input(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise InputError("input: expected a JSON object")
    queries_raw = _require_field(data, "queries", "input", "queries list")
    if not isinstance(queries_raw, list):
        raise InputError("queries: expected a list")
    return [_validate_query(raw, index) for index, raw in enumerate(queries_raw)]


def _hour_window(start: int, end: int) -> Optional[Tuple[int, int]]:
    if end <= start:
        return None
    return (start // HOUR_SECONDS, (end - 1) // HOUR_SECONDS)


def _bucket_intersects_query(
    cx: int,
    cy: int,
    south: float,
    north: float,
    segments: Sequence[Tuple[float, float]],
) -> bool:
    lat_lo, lat_hi = _cell_bounds(LAT_MIN, cy, GRID_DEG, LAT_MAX)
    if lat_hi + INTERSECTION_EPS < south or lat_lo - INTERSECTION_EPS > north:
        return False
    lon_lo, lon_hi = _cell_bounds(LON_MIN, cx, GRID_DEG, LON_MAX)
    for seg_lo, seg_hi in segments:
        if lon_hi + INTERSECTION_EPS >= seg_lo and lon_lo - INTERSECTION_EPS <= seg_hi:
            return True
    return False


def _query_index(
    manifest: IndexVersion,
    reader: BucketReader,
    queries: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    # Index populated cells once per version: hour -> sorted cell_x ->
    # sorted cell_y.  Pruning then slices only cells inside the query box
    # rather than walking every populated cell of every hour it touches.
    groups: Dict[int, Dict[int, List[int]]] = {}
    for (hour, cx, cy) in manifest.buckets:
        groups.setdefault(hour, {}).setdefault(cx, []).append(cy)
    sorted_hours = sorted(groups)
    sorted_x: Dict[int, List[int]] = {}
    for hour, columns in groups.items():
        for cys in columns.values():
            cys.sort()
        sorted_x[hour] = sorted(columns)

    results = []
    for query in queries:
        south = query["south"]
        north = query["north"]
        start = query["start"]
        end = query["end"]
        arc = _make_arc(query["west"], query["east"])

        candidate_blocks = 0
        scanned_points = 0
        matched: List[Dict[str, Any]] = []

        hour_window = _hour_window(start, end)
        # A reversed latitude interval matches nothing.
        if hour_window is not None and south <= north:
            first_hour, last_hour = hour_window
            left = bisect.bisect_left(sorted_hours, first_hour)
            right = bisect.bisect_right(sorted_hours, last_hour)

            y_first, y_last = _cell_range(south, north, LAT_MIN, GRID_DEG, LAT_CELL_COUNT)
            x_ranges = [
                _cell_range(seg_lo, seg_hi, LON_MIN, GRID_DEG, LON_CELL_COUNT)
                for seg_lo, seg_hi in arc.query_segments()
            ]

            for hour in sorted_hours[left:right]:
                columns = groups[hour]
                xs = sorted_x[hour]
                for x_first, x_last in x_ranges:
                    x_from = bisect.bisect_left(xs, x_first)
                    x_to = bisect.bisect_right(xs, x_last)
                    for col in range(x_from, x_to):
                        cx = xs[col]
                        cys = columns[cx]
                        y_from = bisect.bisect_left(cys, y_first)
                        y_to = bisect.bisect_right(cys, y_last)
                        for row in range(y_from, y_to):
                            cy = cys[row]
                            bucket = (hour, cx, cy)
                            if not _bucket_intersects_query(
                                cx, cy, south, north, arc.query_segments()
                            ):
                                continue
                            # A spatial/time bucket actually read by this
                            # query; load it and scan every stored row.
                            records = reader.read(manifest.buckets[bucket])
                            _check_bucket_placement(bucket, records)
                            candidate_blocks += 1
                            scanned_points += len(records)
                            for record in records:
                                if (
                                    start <= record["t"] < end
                                    and south <= record["lat"] <= north
                                    and arc.contains(record["lon"])
                                ):
                                    matched.append(record)

        # Each record lives in exactly one bucket, so the exact predicate
        # already sees it once.  Deduplicate defensively by (device, t): a
        # date-line query can never return the same row twice.
        seen = {}
        for record in matched:
            seen.setdefault((record["t"], record["device"]), record)
        rows_out = sorted(seen.values(), key=lambda record: (record["t"], record["device"]))
        results.append(
            {
                "id": query["id"],
                "rows": rows_out,
                "candidate_blocks": candidate_blocks,
                "scanned_points": scanned_points,
            }
        )

    return {"version": manifest.version, "results": results}


def queries(root: Path, input_path: Path, version: Optional[int] = None) -> Dict[str, Any]:
    query_list = _validate_query_input(_load_json_file(input_path, "query input"))
    head_version = _read_head_version(root)
    if version is None:
        selected_version = head_version
    else:
        selected_version = _validate_integer(version, "--version")
        if selected_version < 0:
            raise InputError("--version: expected a non-negative integer")
        if selected_version > head_version:
            raise InputError(
                "version %d: not published (current version is %d)"
                % (selected_version, head_version)
            )
    manifest = _load_version(root, selected_version)
    return _query_index(manifest, BucketReader(root), query_list)


# ---------------------------------------------------------------------------
# CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Versioned hourly telemetry range store")
    parser.add_argument("root", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("apply")
    stage.add_argument("input", type=Path)
    stage.add_argument("--crash-before-publish", action="store_true")

    scan = subparsers.add_parser("query")
    scan.add_argument("input", type=Path)
    scan.add_argument("--version", type=int)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "apply":
            result = apply(args.root, args.input, args.crash_before_publish)
        else:
            result = queries(args.root, args.input, args.version)
    except _CrashBeforePublish:
        # Deliberate, unclean exit before the head is replaced.
        return 97
    except InputError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
