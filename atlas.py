#!/usr/bin/env python3
"""Deterministic shadow-atlas slot allocation and cascade ownership.

The program keeps three pieces of state between frames:

* a *pinned* light -> atlas-slot map.  A light only gets a new slot when it is
  newly admitted (or when its old slot is no longer valid).  Evicting a
  low-priority light therefore never renumbers the lights that stayed.
* a per-directional-light cascade state.  Cascade boundaries have a
  hysteresis band, so a camera oscillating around a split point does not make
  the shadow selection flicker.
* an optional rectangle packer for atlases with explicitly sized tiles.

Input/output are JSON; see README.md for the full schema.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import sys
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

EPS = 1e-9

DEFAULT_SLOTS = 8
DEFAULT_CASCADE_COUNT = 4
DEFAULT_CASCADE_NEAR = 0.0
DEFAULT_CASCADE_FAR = 100.0
DEFAULT_HYSTERESIS_RATIO = 0.01  # 1% of the full cascade depth

# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _first(mapping: Mapping[str, Any], names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def _num(value: Any, default: float = 0.0) -> float:
    """Best-effort finite float conversion; never lets a bad value crash a frame."""
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _int(value: Any, default: int = 0) -> int:
    number = _num(value, float(default))
    try:
        return int(number)
    except (OverflowError, ValueError):
        return default


def _json_number(value: float) -> Any:
    """Emit integral floats as JSON integers (nicer and stable in output)."""
    if abs(value - round(value)) <= EPS:
        return int(round(value))
    return value


def _canonical_json(value: Any) -> str:
    """Best-effort canonical serialisation used for content-derived identities."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        )
    except (TypeError, ValueError):
        return repr(value)


def _clean_id(value: Any, index: int) -> Tuple[str, Any]:
    """Return (uid, json-safe raw id) for a light identifier.

    Scalar ids are kept as-is so the output echoes the input.  Composite ids
    (lists/dicts) are not valid JSON object keys, so they are canonicalised
    into a stable string instead of being rendered with Python's repr.
    """
    if value is None:
        raw: Any = "light%d" % index
    elif isinstance(value, (str, int, float, bool)):
        raw = value
    else:
        try:
            raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            raw = "light%d" % index
    return str(raw), raw


def _derived_id(raw: Mapping[str, Any]) -> Tuple[str, Any]:
    """Content-derived identity for a light that declares no ``id``.

    Position-based names would make the output depend on the order of the
    ``lights`` list, which breaks the "permuting the list must not change the
    digest" requirement.  Hashing the light's own properties keeps the identity
    stable across permutations instead.
    """
    payload = dict(raw)
    payload.pop("id", None)
    payload.pop("name", None)
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]
    derived = "auto:" + digest
    return derived, derived


def _is_directional(raw: Mapping[str, Any], kind: str) -> bool:
    """Decide whether a light uses the cascade split.

    An explicit flag always wins.  Otherwise the type string decides.  Lights
    with no declared type are treated as directional: the cascade table is
    reported for them too, which is what makes the hysteresis observable when
    the request does not bother to tag the sun.
    """
    for key in ("directional", "is_directional"):
        if raw.get(key) is not None:
            return bool(raw[key])
    k = kind.strip().lower()
    if k in ("dir", "directional", "sun", "sunlight"):
        return True
    if k in (
        "point",
        "spot",
        "omni",
        "pointlight",
        "spotlight",
        "area",
        "area_light",
        "arealight",
        "rect",
        "tube",
    ):
        return False
    # Some scenes call the cascade switch "cascade" on the light itself.
    if raw.get("cascade") is not None and not isinstance(raw.get("cascade"), (list, dict)):
        return bool(raw.get("cascade"))
    return True


def _tile_size(raw: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    """Return an explicitly requested tile size, if the light has one."""
    size = _first(
        raw,
        (
            "tile",
            "tile_size",
            "shadow_tile",
            "size",
            "resolution",
            "shadow_resolution",
            "shadow_size",
            "res",
        ),
    )
    if size is None:
        width = _first(raw, ("tile_w", "width", "shadow_width"))
        height = _first(raw, ("tile_h", "height", "shadow_height"))
        if width is not None or height is not None:
            w = _num(width, _num(height))
            h = _num(height, w)
            return (w, h)
        return None

    if isinstance(size, str):
        text = size.lower().replace(",", "x").replace("*", "x")
        parts = [p for p in text.split("x") if p.strip()]
        if len(parts) == 1:
            value = _num(parts[0], 0.0)
            return (value, value)
        if len(parts) >= 2:
            return (_num(parts[0], 0.0), _num(parts[1], 0.0))
        return (0.0, 0.0)
    if isinstance(size, Mapping):
        w = _num(_first(size, ("w", "width", "x_size")), 0.0)
        h = _num(_first(size, ("h", "height", "y_size")), w)
        return (w, h)
    if isinstance(size, (list, tuple)):
        if not size:
            return (0.0, 0.0)
        w = _num(size[0], 0.0)
        h = _num(size[1], 0.0) if len(size) > 1 else w
        return (w, h)
    value = _num(size, 0.0)
    return (value, value)


@dataclass(frozen=True)
class Light:
    uid: str
    raw_id: Any
    prio: float
    influence: float
    dist: float
    kind: str
    directional: bool
    tile: Optional[Tuple[float, float]]
    raw: Mapping[str, Any]
    anonymous: bool = False


def _normalize_light(raw: Any, index: int) -> Tuple[Optional[Light], Optional[str]]:
    if isinstance(raw, str) or isinstance(raw, (int, float)):
        raw = {"id": raw}
    if not isinstance(raw, Mapping):
        return None, "invalid_light"

    requested_id = raw.get("id", raw.get("name"))
    anonymous = requested_id is None
    if anonymous:
        uid, raw_id = _derived_id(raw)
    else:
        uid, raw_id = _clean_id(requested_id, index)
    kind = str(_first(raw, ("type", "kind", "light_type"), ""))
    light = Light(
        uid=uid,
        raw_id=raw_id,
        prio=_num(_first(raw, ("prio", "priority", "importance")), 0.0),
        influence=_num(
            _first(raw, ("influence", "screen_influence", "screen_area", "area")), 0.0
        ),
        dist=_num(_first(raw, ("dist", "distance", "depth")), 0.0),
        kind=kind,
        directional=_is_directional(raw, kind),
        tile=_tile_size(raw),
        raw=raw,
        anonymous=anonymous,
    )
    return light, None


def _merge_light(base: Light, update: Mapping[str, Any]) -> Light:
    """Apply a per-frame property override while retaining the stable identity.

    The identity must survive the override: re-deriving an anonymous light's
    id from its updated properties would change the uid as priorities animate,
    and both the pinned slot and the cascade state would be lost mid-scene.
    """
    merged = dict(base.raw)
    merged.update(update)
    if base.anonymous:
        merged.pop("id", None)
        merged.pop("name", None)
        merged["id"] = None
    else:
        merged["id"] = base.raw_id
    light, _ = _normalize_light(merged, 0)
    assert light is not None
    if light.uid != base.uid:
        light = replace(light, uid=base.uid, raw_id=base.raw_id,
                        anonymous=base.anonymous)
    return light


def _lights_for_frame(base: Sequence[Light], frame: Mapping[str, Any]) -> List[Light]:
    """Support static lights (the documented form) and optional per-frame overrides.

    ``frame["lights"]`` may be a complete list or a ``{id: {...}}`` update map.
    Nothing in the documented input schema requires this, but it makes the
    eviction/stability rules testable when priorities animate.
    """
    override = _first(frame, ("lights", "light_updates", "lights_update"))
    if override is None:
        return list(base)

    if isinstance(override, Mapping) and not any(
        key in override for key in ("id", "type", "kind", "prio", "priority")
    ):
        updates = override
        result: List[Light] = []
        seen = set()
        for light in base:
            change = updates.get(light.uid)
            if change is None:
                change = updates.get(light.raw_id)
            if isinstance(change, Mapping):
                result.append(_merge_light(light, change))
            else:
                result.append(light)
            seen.add(light.uid)
        # Updates for unknown ids introduce new lights.
        for uid, change in updates.items():
            if str(uid) in seen or not isinstance(change, Mapping):
                continue
            raw = dict(change)
            raw.setdefault("id", uid)
            light, _ = _normalize_light(raw, len(result))
            if light is not None:
                result.append(light)
        return result

    if isinstance(override, (list, tuple)):
        result = []
        seen_ids = set()
        for i, item in enumerate(override):
            light, error = _normalize_light(item, i)
            if light is None:
                continue
            if light.uid in seen_ids:
                continue
            seen_ids.add(light.uid)
            result.append(light)
        return result
    return list(base)


def _normalize_lights(raw_lights: Any) -> Tuple[List[Light], List[Dict[str, Any]]]:
    lights: List[Light] = []
    failures: List[Dict[str, Any]] = []
    seen: Dict[str, Light] = {}
    if not isinstance(raw_lights, (list, tuple)):
        return lights, failures
    for index, raw in enumerate(raw_lights):
        light, error = _normalize_light(raw, index)
        if light is None:
            failures.append({"frame": None, "light": None, "reason": error or "invalid_light"})
            continue
        old = seen.get(light.uid)
        if old is not None:
            # Two lights with no id and byte-identical properties are
            # indistinguishable after any permutation of the list, so there
            # is no order-stable way to tell them apart.  Report instead of
            # silently inventing a positional name.
            failures.append(
                {
                    "frame": None,
                    "light": light.raw_id,
                    "reason": "duplicate_anonymous_light"
                    if light.anonymous
                    else "duplicate_light_id",
                    "detail": {"id": light.uid},
                }
            )
            continue
        seen[light.uid] = light
        lights.append(light)
    return lights, failures


def _rank_key(light: Light) -> Tuple[float, float, float, str]:
    # Priority first, then screen influence, then distance (nearer first).
    # The final uid tie-break makes allocation independent of input ordering.
    return (-light.prio, -light.influence, light.dist, light.uid)


# ---------------------------------------------------------------------------
# Stable slot allocation
# ---------------------------------------------------------------------------


class StableSlotAllocator:
    """Pins a light to a slot until the light is gone or the slot disappears."""

    def __init__(self, slots: int) -> None:
        self.slots = max(0, int(slots))
        self._pinned: Dict[str, int] = {}
        self._last_seen: Dict[str, int] = {}
        self._frame = 0

    def assign(self, active: Sequence[Light]) -> Dict[str, int]:
        """Return {light uid: slot} for the active list.

        Existing assignments are kept verbatim.  A light that was evicted
        earlier reclaims its old slot if that slot is still free; otherwise a
        newly admitted light receives the lowest free slot, in ranking order.
        Because pass 1 never moves a still-active light, admitting or evicting
        a light cannot renumber any other light.
        """
        slots = self.slots
        result: Dict[str, int] = {}
        used = set()

        # Pass 1: honour every pin that is still valid.
        for light in active:
            slot = self._pinned.get(light.uid)
            if slot is None or slot < 0 or slot >= slots or slot in used:
                continue
            result[light.uid] = slot
            used.add(slot)

        # Pass 2: returning lights reclaim their previous slot when possible.
        pending: List[Light] = []
        for light in active:
            if light.uid in result:
                continue
            slot = self._pinned.get(light.uid)
            if slot is not None and 0 <= slot < slots and slot not in used:
                result[light.uid] = slot
                used.add(slot)
            else:
                pending.append(light)

        # Pass 3: deterministic first-fit for genuinely new lights.
        free = [s for s in range(slots) if s not in used]
        for light in pending:
            if not free:
                break
            slot = free.pop(0)
            result[light.uid] = slot
            used.add(slot)

        self._frame += 1
        for uid, slot in result.items():
            self._pinned[uid] = slot
            self._last_seen[uid] = self._frame
        self._prune()
        return result

    def _prune(self) -> None:
        """Bound the reservation table so a long-lived scene cannot grow forever.

        Reservations are a convenience for lights that come back; anything that
        has been absent for a while is dropped.  The window is generous enough
        that normal flicker in/out of the slot budget still gets the old slot.
        """
        keep = max(64, self.slots * 8)
        if len(self._pinned) <= keep:
            return
        stale = sorted(
            self._last_seen, key=lambda uid: (self._last_seen[uid], uid)
        )[: len(self._pinned) - keep]
        for uid in stale:
            self._pinned.pop(uid, None)
            self._last_seen.pop(uid, None)


# ---------------------------------------------------------------------------
# Cascade configuration and hysteresis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CascadeConfig:
    bounds: Tuple[float, ...]
    hysteresis: float

    @property
    def count(self) -> int:
        return max(1, len(self.bounds) - 1)


def _sorted_unique(values: Iterable[float]) -> List[float]:
    result: List[float] = []
    for value in sorted(values):
        if not math.isfinite(value):
            continue
        if not result or abs(value - result[-1]) > EPS:
            result.append(value)
    return result


def _parse_cascade(req: Mapping[str, Any]) -> CascadeConfig:
    raw = _first(
        req,
        (
            "cascade",
            "cascades",
            "shadow_cascade",
            "shadow_cascades",
            "cascade_config",
        ),
    )
    config = raw if isinstance(raw, Mapping) else {}

    near = _num(
        _first(
            config,
            ("near", "z_near", "min_z", "n"),
            _first(req, ("cascade_near", "z_near", "near"), DEFAULT_CASCADE_NEAR),
        ),
        DEFAULT_CASCADE_NEAR,
    )
    far = _num(
        _first(
            config,
            ("far", "z_far", "max_z", "f"),
            _first(req, ("cascade_far", "z_far", "far"), DEFAULT_CASCADE_FAR),
        ),
        DEFAULT_CASCADE_FAR,
    )
    if far <= near:
        far = near + max(1.0, abs(near) * 0.01)

    count_raw = _first(
        config,
        ("count", "levels", "num", "cascade_count"),
        _first(req, ("cascade_count", "num_cascades", "cascade_levels", "shadow_cascade_count")),
    )

    # Full edges, interior splits, and the shorthand list/string/int forms.
    edges_raw = _first(
        config,
        ("bounds", "edges", "boundaries", "cascade_bounds", "cascade_edges"),
        _first(req, ("cascade_bounds", "cascade_edges", "cascade_boundaries")),
    )
    splits_raw = _first(
        config,
        ("splits", "split_points", "cascade_splits"),
        _first(req, ("cascade_splits", "split_points")),
    )

    list_raw: Optional[Sequence[Any]] = None
    if raw is not None and not isinstance(raw, Mapping):
        if isinstance(raw, str):
            try:
                count_raw = int(float(raw))
            except ValueError:
                count_raw = None
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            count_raw = int(raw)
        elif isinstance(raw, (list, tuple)):
            list_raw = raw

    edges: Optional[List[float]] = None
    if isinstance(edges_raw, (list, tuple)):
        candidate = _sorted_unique(_num(v) for v in edges_raw)
        if len(candidate) >= 2:
            edges = candidate
        elif len(candidate) == 1:
            edges = _sorted_unique([near, candidate[0], far])

    if edges is None and isinstance(splits_raw, (list, tuple)):
        interior = _sorted_unique(
            _num(v) for v in splits_raw if near - EPS < _num(v) < far + EPS
        )
        edges = _sorted_unique([near] + interior + [far])

    if edges is None and list_raw is not None:
        seq = [_num(v) for v in list_raw]
        seq = [v for v in seq if math.isfinite(v)]
        if seq and all(seq[i] <= seq[i + 1] + EPS for i in range(len(seq) - 1)):
            seq = _sorted_unique(seq)
            count = _int(count_raw, -1) if count_raw is not None else -1
            if count > 0 and len(seq) == count + 1:
                edges = seq
            elif count > 0 and len(seq) == count - 1:
                edges = _sorted_unique([near] + seq + [far])
            elif len(seq) >= 2 and seq[0] <= near + EPS:
                # A list beginning at the near plane is a full edge list.
                edges = seq
            else:
                # Otherwise treat the list as interior split points.
                edges = _sorted_unique([near] + seq + [far])

    count = _int(count_raw, -1) if count_raw is not None else -1
    if edges is None:
        if count < 1:
            count = DEFAULT_CASCADE_COUNT
        count = max(1, min(count, 64))
        if count == 1:
            edges = [near, far]
        else:
            step = (far - near) / float(count)
            edges = [near + step * i for i in range(count)] + [far]
    else:
        edges = _sorted_unique(edges)
        if len(edges) < 2:
            edges = [near, far]

    hyst_raw = _first(
        config,
        ("hysteresis", "hysteresis_width", "overlap", "overlap_width", "band"),
        _first(
            req,
            ("cascade_hysteresis", "hysteresis", "cascade_overlap", "cascade_overlap_width"),
        ),
    )
    ratio_raw = _first(
        config,
        ("hysteresis_ratio", "overlap_ratio", "hysteresis_fraction"),
        _first(req, ("cascade_hysteresis_ratio", "hysteresis_ratio", "cascade_overlap_ratio")),
    )

    if ratio_raw is not None:
        ratio = _num(ratio_raw, DEFAULT_HYSTERESIS_RATIO)
        if not math.isfinite(ratio) or ratio < 0:
            ratio = 0.0
        hysteresis = ratio * (edges[-1] - edges[0])
    elif hyst_raw is not None:
        if isinstance(hyst_raw, Mapping):
            ratio = _num(_first(hyst_raw, ("ratio", "fraction")), -1.0)
            if ratio >= 0:
                hysteresis = ratio * (edges[-1] - edges[0])
            else:
                hysteresis = _num(_first(hyst_raw, ("width", "value", "absolute")), 0.0)
        else:
            # A bare number is an absolute world-space width.
            hysteresis = _num(hyst_raw, 0.0)
    else:
        hysteresis = DEFAULT_HYSTERESIS_RATIO * (edges[-1] - edges[0])

    if not math.isfinite(hysteresis) or hysteresis < 0:
        hysteresis = 0.0
    return CascadeConfig(bounds=tuple(edges), hysteresis=hysteresis)


class CascadeSelector:
    """Cascade index per directional light with a two-sided hysteresis band."""

    def __init__(self, config: CascadeConfig) -> None:
        self.config = config
        self._state: Dict[str, int] = {}
        self.switch_count: Dict[str, int] = {}
        self.switches: List[Dict[str, Any]] = []

    def _initial(self, z: float) -> int:
        # bisect_left puts a camera exactly on a split into the *lower*
        # cascade.  That is the stable tie rule for the overlap region.
        index = bisect.bisect_left(self.config.bounds, z) - 1
        return max(0, min(index, self.config.count - 1))

    def select(self, uid: str, raw_id: Any, z: float, frame: int) -> int:
        if self.config.count <= 1:
            self._state[uid] = 0
            return 0

        # A light that was just (re)admitted has no ownership history: it
        # starts in the cascade that contains the camera right now.  Using
        # the tie rule here keeps "first frame" deterministic for any list
        # order and any permutation.
        fresh = uid not in self._state
        previous = self._state.get(uid)
        if fresh:
            index = self._initial(z)
        else:
            index = previous
            band = self.config.hysteresis
            while True:
                if (
                    index < self.config.count - 1
                    and z - self.config.bounds[index + 1] > band + EPS
                ):
                    index += 1
                    continue
                if index > 0 and self.config.bounds[index] - z > band + EPS:
                    index -= 1
                    continue
                break

        if not fresh and index != previous:
            self.switch_count[uid] = self.switch_count.get(uid, 0) + 1
            self.switches.append(
                {
                    "frame": frame,
                    "light": raw_id,
                    "from": previous,
                    "to": index,
                }
            )
        self._state[uid] = index
        return index


# ---------------------------------------------------------------------------
# Optional atlas rectangle packing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AtlasConfig:
    width: float
    height: float
    granularity: float
    gap: float
    exact_sizes: bool


def _parse_atlas(req: Mapping[str, Any]) -> Optional[AtlasConfig]:
    raw = _first(req, ("atlas", "shadow_atlas", "atlas_config"))
    config = raw if isinstance(raw, Mapping) else {}
    width = _first(
        config,
        ("width", "w", "atlas_width"),
        _first(req, ("atlas_width", "atlas_w")),
    )
    height = _first(
        config,
        ("height", "h", "atlas_height"),
        _first(req, ("atlas_height", "atlas_h")),
    )
    if width is None and isinstance(raw, (list, tuple)) and len(raw) >= 2:
        width, height = raw[0], raw[1]
    if width is None and height is None:
        return None

    gran = _num(
        _first(
            config,
            ("granularity", "quantum", "alignment", "step"),
            _first(req, ("atlas_granularity", "tile_granularity"), 1.0),
        ),
        1.0,
    )
    gap = _num(
        _first(config, ("gap", "padding", "spacing"), _first(req, ("atlas_gap",), 0.0)),
        0.0,
    )
    exact = bool(
        _first(
            config,
            ("exact_sizes", "indivisible", "no_resize", "strict"),
            _first(req, ("atlas_exact_sizes", "indivisible_tiles"), False),
        )
    )
    w = _num(width, 0.0)
    h = _num(height, 0.0)
    if w <= 0 or h <= 0:
        return None
    if gran <= 0:
        gran = 1.0
    return AtlasConfig(width=w, height=h, granularity=gran, gap=max(0.0, gap), exact_sizes=exact)


def _quantize(value: float, granularity: float) -> float:
    if granularity <= 0:
        return value
    return math.ceil((value - EPS) / granularity) * granularity


def _pack_frame(
    active: Sequence[Light], atlas: AtlasConfig
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """First-fit shelf pack the active lights in priority order.

    Each light keeps its requested tile when the atlas is flexible (the tile
    is only rounded up to the next multiple of ``granularity``).  With
    ``exact_sizes`` a tile that is not a whole multiple of the granularity is
    refused: a partial cell cannot be subdivided, so the light is reported as
    ``indivisible_size`` instead of silently stealing space from a neighbour.

    Packing failure never aborts the frame: the light is reported in
    ``failures`` and keeps its logical slot, but has no atlas region.
    """
    regions: Dict[str, Dict[str, Any]] = {}
    failures: List[Dict[str, Any]] = []
    # Shelf state: [row_top, row_height, cursor_x].  Rows are tried in
    # insertion order, which makes the result deterministic for a given
    # priority order.
    rows: List[List[float]] = []

    for light in active:
        if light.tile is None:
            continue
        requested_w, requested_h = light.tile
        if requested_w <= 0 or requested_h <= 0:
            failures.append(
                {
                    "light": light.raw_id,
                    "reason": "invalid_tile",
                    "detail": {"tile": [_json_number(requested_w), _json_number(requested_h)]},
                }
            )
            continue

        if atlas.exact_sizes:
            divisible = True
            for value in (requested_w, requested_h):
                ratio = value / atlas.granularity
                if abs(ratio - round(ratio)) > EPS:
                    divisible = False
                    break
            if not divisible:
                failures.append(
                    {
                        "light": light.raw_id,
                        "reason": "indivisible_size",
                        "detail": {
                            "tile": [_json_number(requested_w), _json_number(requested_h)],
                            "granularity": _json_number(atlas.granularity),
                        },
                    }
                )
                continue

        tile_w = _quantize(requested_w, atlas.granularity)
        tile_h = _quantize(requested_h, atlas.granularity)
        if tile_w > atlas.width + EPS or tile_h > atlas.height + EPS:
            failures.append(
                {
                    "light": light.raw_id,
                    "reason": "oversize",
                    "detail": {
                        "tile": [_json_number(requested_w), _json_number(requested_h)],
                        "atlas": [_json_number(atlas.width), _json_number(atlas.height)],
                    },
                }
            )
            continue

        placement: Optional[Tuple[float, float]] = None
        for row in rows:
            row_top, row_height, cursor = row
            x = cursor + atlas.gap if cursor > EPS else 0.0
            if x + tile_w <= atlas.width + EPS and tile_h <= row_height + EPS:
                placement = (x, row_top)
                row[2] = x + tile_w
                break

        if placement is None:
            # Cap the row to the atlas width; any leftover is unusable.
            if rows:
                top = rows[-1][0] + rows[-1][1] + atlas.gap
            else:
                top = 0.0
            if top + tile_h <= atlas.height + EPS:
                rows.append([top, tile_h, tile_w])
                placement = (0.0, top)
            else:
                failures.append(
                    {
                        "light": light.raw_id,
                        "reason": "pack_failed",
                        "detail": {
                            "tile": [_json_number(requested_w), _json_number(requested_h)],
                            "atlas": [_json_number(atlas.width), _json_number(atlas.height)],
                            "at": [0, _json_number(top)],
                        },
                    }
                )
                continue

        x, y = placement
        region: Dict[str, Any] = {
            "x": _json_number(x),
            "y": _json_number(y),
            "w": _json_number(tile_w),
            "h": _json_number(tile_h),
        }
        if abs(tile_w - requested_w) > EPS or abs(tile_h - requested_h) > EPS:
            # Flexible mode had to grow the tile to satisfy the granularity.
            region["requested"] = [_json_number(requested_w), _json_number(requested_h)]
        regions[light.uid] = region

    return regions, failures


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def _sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _allocation_digest(
    alloc: Mapping[str, Mapping[str, int]],
    evict: Sequence[Any],
    failures: Sequence[Mapping[str, Any]],
    regions: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]],
) -> str:
    frames = []
    for frame in sorted(alloc, key=lambda value: int(value)):
        frames.append(
            [
                int(frame),
                [[uid, int(slot)] for uid, slot in sorted(alloc[frame].items())],
            ]
        )
    canonical_failures = []
    for failure in failures:
        canonical_failures.append(
            {
                "frame": failure.get("frame"),
                "light": str(failure.get("light")),
                "reason": failure.get("reason"),
            }
        )
    canonical_failures.sort(
        key=lambda item: (
            -1 if item["frame"] is None else int(item["frame"]),
            item["light"],
            str(item["reason"]),
        )
    )
    payload: Dict[str, Any] = {
        "alloc": frames,
        "evict": sorted(str(value) for value in evict),
        "failures": canonical_failures,
    }
    if regions is not None:
        payload["regions"] = [
            [
                int(frame),
                [
                    [uid, [r["x"], r["y"], r["w"], r["h"]]]
                    for uid, r in sorted(regions[frame].items())
                ],
            ]
            for frame in sorted(regions, key=lambda value: int(value))
        ]
    return _sha256(payload)


def _cascade_digest(cascade: Mapping[str, Mapping[str, int]]) -> str:
    payload = [
        [
            int(frame),
            [[uid, int(index)] for uid, index in sorted(cascade[frame].items())],
        ]
        for frame in sorted(cascade, key=lambda value: int(value))
    ]
    return _sha256(payload)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _overlap_bands(config: CascadeConfig) -> List[List[Any]]:
    """Return the hysteresis band around each interior split.

    ``[lo, hi, lower, upper]``: a camera within the band stays in whichever
    cascade owned it last; entering from below flips to ``lower`` and entering
    from above flips to ``upper``.  Directly on the split the camera belongs
    to the lower cascade (the documented tie rule).
    """
    bands: List[List[Any]] = []
    if config.hysteresis <= 0:
        return bands
    low_edge = config.bounds[0]
    high_edge = config.bounds[-1]
    for i in range(1, config.count):
        split = config.bounds[i]
        bands.append(
            [
                _json_number(max(low_edge, split - config.hysteresis)),
                _json_number(min(high_edge, split + config.hysteresis)),
                i - 1,
                i,
            ]
        )
    return bands


def run(req: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(req, Mapping):
        req = {}

    slots = max(0, _int(_first(req, ("slots", "slot_count", "atlas_slots"), DEFAULT_SLOTS), DEFAULT_SLOTS))
    lights, failures = _normalize_lights(req.get("lights", []))

    frames_raw = req.get("frames")
    if frames_raw is None:
        frames: List[Mapping[str, Any]] = [{}]
    elif isinstance(frames_raw, Mapping):
        frames = [frames_raw]
    elif isinstance(frames_raw, (list, tuple)):
        frames = [f if isinstance(f, Mapping) else {} for f in frames_raw]
    else:
        frames = [{}]
    if not frames:
        frames = [{}]

    cascade_config = _parse_cascade(req)
    cascade_selector = CascadeSelector(cascade_config)
    allocator = StableSlotAllocator(slots)
    atlas = _parse_atlas(req)

    alloc: Dict[str, Dict[str, int]] = {}
    cascade: Dict[str, Dict[str, int]] = {}
    order: Dict[str, List[Any]] = {}
    evict_frames: Dict[str, List[Any]] = {}
    regions_by_frame: Dict[str, Dict[str, Dict[str, Any]]] = {}
    evict: List[Any] = []
    evict_seen = set()

    for frame_index, frame in enumerate(frames):
        frame_key = str(frame_index)
        current_lights = _lights_for_frame(lights, frame)
        ranked = sorted(current_lights, key=_rank_key)
        active = ranked[:slots]
        evicted = ranked[slots:]

        slot_map = allocator.assign(active)
        alloc[frame_key] = {light.uid: slot_map[light.uid] for light in active}
        order[frame_key] = [light.raw_id for light in ranked]
        evict_frames[frame_key] = [light.raw_id for light in evicted]
        for light in evicted:
            if light.uid not in evict_seen:
                evict_seen.add(light.uid)
                evict.append(light.raw_id)

        z = _num(
            _first(
                frame,
                ("cam_z", "camera_z", "z"),
                _camera_z(_first(frame, ("cam", "camera", "view"))),
            ),
            0.0,
        )
        frame_cascade: Dict[str, int] = {}
        for light in active:
            if light.directional:
                index = cascade_selector.select(
                    light.uid, light.raw_id, z, frame_index
                )
            else:
                # Point/spot shadows already live in their own atlas tile; the
                # cascade field is present for schema compatibility only.
                index = 0
            frame_cascade[light.uid] = index
        cascade[frame_key] = frame_cascade

        if atlas is not None:
            regions, pack_failures = _pack_frame(active, atlas)
            regions_by_frame[frame_key] = regions
            for failure in pack_failures:
                failure = dict(failure)
                failure["frame"] = frame_index
                failures.append(failure)

    output: Dict[str, Any] = {
        "alloc": alloc,
        "evict": evict,
        "cascade": cascade,
        "order": order,
        "evict_frames": evict_frames,
        "cascade_switch_count": cascade_selector.switch_count,
        "cascade_switches": cascade_selector.switches,
        "cascade_config": {
            "bounds": [_json_number(value) for value in cascade_config.bounds],
            "hysteresis": _json_number(cascade_config.hysteresis),
            # Overlap band at each interior split: [lo, hi, lower, upper].
            # Inside the band the previous owner is kept (stable ownership);
            # directly on the split the camera belongs to the lower cascade.
            "overlaps": _overlap_bands(cascade_config),
            "tie_rule": "lower",
        },
        "failures": failures,
        "digest": _allocation_digest(
            alloc,
            evict,
            failures,
            regions_by_frame if atlas is not None else None,
        ),
        "cascade_digest": _cascade_digest(cascade),
    }
    if atlas is not None:
        output["regions"] = regions_by_frame
        output["atlas"] = {
            "width": _json_number(atlas.width),
            "height": _json_number(atlas.height),
            "granularity": _json_number(atlas.granularity),
            "gap": _json_number(atlas.gap),
            "exact_sizes": atlas.exact_sizes,
        }
    return output


def _camera_z(camera: Any) -> Any:
    if isinstance(camera, Mapping):
        return _first(camera, ("cam_z", "z", "near_z"))
    if isinstance(camera, (list, tuple)) and len(camera) >= 3:
        return camera[2]
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        sys.stderr.write("usage: python3 atlas.py req.json output.json\n")
        return 2
    try:
        with open(args[0], "r", encoding="utf-8") as handle:
            req = json.load(handle)
    except (OSError, ValueError) as exc:
        sys.stderr.write("failed to read input: %s\n" % exc)
        return 2

    result = run(req)
    try:
        with open(args[1], "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
    except OSError as exc:
        sys.stderr.write("failed to write output: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

