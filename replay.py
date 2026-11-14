#!/usr/bin/env python3
"""Fold an event stream into aggregate state with versioned upcasters.

Command line (compatible with the original tool)::

    python3 replay.py events.json output.json [--snapshot snap.json]
                                             [--fail-after n] [--shards n]

Every event is first migrated to the canonical payload version of its action
type through a registered upcaster chain; only migrated events are folded.
Because the fold is a pure function of the *migrated* event prefix, the same
prefix always folds to the same state, and a snapshot never caches a raw,
un-migrated payload.

Canonical payload shapes understood by the folder:

* ``credit`` / ``debit``: ``{"amount": "<decimal yuan string>"}`` (v3 shape)
* ``rename``:             ``{"name": "<string>"}`` (version independent)

A registered upcaster chain must end in one of those shapes.

Library API -- everything below is re-entrant and shares no mutable state, so
independent streams can be replayed concurrently (threads or processes)::

    from replay import (ReplayResult, fold_events, fold_shards,
                        merge_results)

    merged  = merge_results([fold_events(a), fold_events(b)])
    sharded = fold_shards([stream[:k], stream[k:]])   # same output as above
    resumed = fold_events(rest, initial=ReplayResult.from_snapshot(snapshot))
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

__all__ = [
    "ACTION_TYPES",
    "DEFAULT_REGISTRY",
    "MigrationError",
    "MissingUpcasterPath",
    "ReplayEngine",
    "ReplayInterrupted",
    "ReplayResult",
    "UpcastStep",
    "UpcasterRegistry",
    "build_default_registry",
    "fold_events",
    "fold_shards",
    "iter_replay_records",
    "load_events",
    "load_snapshot",
    "main",
    "merge_results",
    "resume_from_snapshot",
    "split_events",
    "write_json",
]

#: Action types the folder understands.
ACTION_TYPES = ("credit", "debit", "rename")

#: Version of the canonical payload for each action type.  ``rename`` payloads
#: are version independent: ``data.name`` has the same meaning in every
#: generation, so a rename event is never upcast (its migration path is just
#: the version it arrived with).
CANONICAL_VERSION = {"credit": 3, "debit": 3, "rename": 4}

#: Action types whose payload is version independent (``data.name`` means the
#: same thing in every generation).  Their migration path is just the version
#: the event arrived with; any version is accepted for them.
VERSION_INDEPENDENT = frozenset({"rename"})

#: Explicit version allowlists for version independent action types (kept for
#: callers that want to be stricter than ``VERSION_INDEPENDENT``).
ACCEPTED_VERSIONS: Dict[str, Sequence[int]] = {}

DEFAULT_STATE = {"balance_cents": 0, "name": "", "applied": 0}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MigrationError(Exception):
    """An event could not be migrated or folded.

    ``reason`` is the stable, machine readable value that ends up in the
    ``rejects`` list; ``detail`` is only used for diagnostics on stderr.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class MissingUpcasterPath(MigrationError):
    """No registered upcaster chain connects the event version to canonical."""


class ReplayInterrupted(Exception):
    """``--fail-after`` stopped a fold; carries the committed event prefix."""

    def __init__(self, result: "ReplayResult", processed: int) -> None:
        super().__init__(f"replay interrupted after {processed} event(s)")
        self.result = result
        self.processed = processed


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _new_state() -> Dict[str, Any]:
    return dict(DEFAULT_STATE)


def _normalise_state(state: Any) -> Dict[str, Any]:
    """Copy a (possibly restored) state while keeping the documented schema."""
    out = _new_state()
    if not isinstance(state, dict):
        return out
    try:
        out["balance_cents"] = int(state.get("balance_cents", 0))
    except (TypeError, ValueError):
        out["balance_cents"] = 0
    try:
        out["applied"] = int(state.get("applied", 0))
    except (TypeError, ValueError):
        out["applied"] = 0
    name = state.get("name", "")
    out["name"] = name if isinstance(name, str) else str(name)
    return out


def _as_plain_int(value: Any) -> Optional[int]:
    """Return ``value`` as an int when it is an integral JSON number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _normalise_version(value: Any) -> Optional[int]:
    return _as_plain_int(value)


def _version_label(value: Any) -> str:
    """Render a version for ``applied_by_version`` keys and reject reasons."""
    normalised = _normalise_version(value)
    if normalised is not None:
        return str(normalised)
    if value is None:
        return "?"
    return str(value)


def _as_integer_cents(value: Any) -> int:
    """Read an integer number of cents from a legacy payload field."""
    if isinstance(value, bool) or value is None:
        raise MigrationError("invalid-amount", f"expected cents, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise MigrationError("invalid-amount", f"expected whole cents, got {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise MigrationError("invalid-amount", "empty amount")
        try:
            amount = Decimal(text)
        except InvalidOperation:
            raise MigrationError("invalid-amount", f"expected cents, got {value!r}")
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise MigrationError("invalid-amount", f"expected whole cents, got {value!r}")
        return int(amount)
    raise MigrationError("invalid-amount", f"expected cents, got {type(value).__name__}")


def _yuan_to_cents(value: Any) -> int:
    """Convert a decimal-yuan string (``"1.50"``) to integer cents (``150``)."""
    if not isinstance(value, str):
        raise MigrationError("invalid-amount", f"expected a decimal yuan string, got {value!r}")
    text = value.strip()
    if not text:
        raise MigrationError("invalid-amount", "empty amount")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise MigrationError("invalid-amount", f"not a decimal amount: {value!r}")
    if not amount.is_finite():
        raise MigrationError("invalid-amount", f"not a finite amount: {value!r}")
    # Sub-cent values are rounded half-up; normal two-decimal amounts are exact.
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _cents_to_yuan(cents: int) -> str:
    """Render integer cents in the canonical decimal-yuan form."""
    return str(Decimal(cents).scaleb(-2))


# ---------------------------------------------------------------------------
# Upcaster registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpcastStep:
    """A single, chained upcaster: ``from_version`` -> ``to_version``."""

    from_version: int
    to_version: int
    handler: Callable[[Mapping[str, Any]], Mapping[str, Any]]


class UpcasterRegistry:
    """Version-registered upcaster chains, keyed by ``(action, from_version)``.

    The registry is immutable once :meth:`freeze` is called, which makes a
    single instance safe to share between concurrently replayed streams.  Use
    :meth:`copy` to derive a registry with extra upcasters.

    ``accepted_versions`` optionally restricts the origin versions allowed for
    an action; it is only needed for actions that are *not* listed in
    :data:`VERSION_INDEPENDENT`.
    """

    def __init__(
        self,
        canonical_version: Optional[Mapping[str, int]] = None,
        accepted_versions: Optional[Mapping[str, Sequence[int]]] = None,
    ) -> None:
        self._canonical: Dict[str, int] = dict(canonical_version or CANONICAL_VERSION)
        self._accepted: Dict[str, tuple] = {
            action: tuple(versions)
            for action, versions in (accepted_versions or ACCEPTED_VERSIONS).items()
        }
        self._steps: Dict[tuple, UpcastStep] = {}
        self._frozen = False

    # -- construction -------------------------------------------------------

    def register(
        self,
        action: str,
        from_version: Any,
        to_version: Any,
        handler: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        *,
        replace: bool = False,
    ) -> "UpcasterRegistry":
        """Register (or replace) one upcaster step for ``action``."""
        if self._frozen:
            raise RuntimeError("registry is frozen; call copy() before registering")
        from_int, to_int = _normalise_version(from_version), _normalise_version(to_version)
        if from_int is None or to_int is None:
            raise ValueError(f"versions must be integers: {from_version!r} -> {to_version!r}")
        if to_int <= from_int:
            raise ValueError(f"upcasters must increase the version: v{from_int} -> v{to_int}")
        if action not in self._canonical:
            raise ValueError(f"unknown action type {action!r}")
        key = (action, from_int)
        if key in self._steps and not replace:
            raise ValueError(f"upcaster already registered for {action!r} v{from_int}")
        self._steps[key] = UpcastStep(from_int, to_int, handler)
        return self

    def freeze(self) -> "UpcasterRegistry":
        self._frozen = True
        return self

    def set_canonical(self, action: str, version: Any) -> "UpcasterRegistry":
        """Move the canonical version of ``action`` forward (e.g. v4 -> v5)."""
        if self._frozen:
            raise RuntimeError("registry is frozen; call copy() before mutating")
        target = _normalise_version(version)
        if target is None:
            raise ValueError(f"canonical version must be an integer: {version!r}")
        if action not in self._canonical:
            raise ValueError(f"unknown action type {action!r}")
        self._canonical[action] = target
        return self

    def copy(self) -> "UpcasterRegistry":
        clone = UpcasterRegistry(self._canonical, self._accepted)
        clone._steps = dict(self._steps)
        return clone

    # -- lookup -------------------------------------------------------------

    def canonical_version(self, action: str) -> Optional[int]:
        return self._canonical.get(action)

    def resolve(self, action: str, version: Any) -> tuple:
        """Return ``(canonical_version, [UpcastStep, ...])`` for an event.

        Raises :class:`MissingUpcasterPath` when the version cannot reach the
        canonical version -- a gap must never be silently skipped.
        """
        target = self._canonical.get(action)
        current = _normalise_version(version)
        if target is None:
            raise MissingUpcasterPath(
                f"no-upcast-path:v{_version_label(version)}->v?",
                f"no canonical version registered for action {action!r}",
            )

        if action in VERSION_INDEPENDENT:
            # ``data`` is self describing: no upcasting is required, but the
            # event must at least declare a version.
            if version is None:
                raise MissingUpcasterPath(
                    f"no-upcast-path:v?->v{target}",
                    f"{action} event is missing its version",
                )
            return (current if current is not None else target), []

        if action in self._accepted:
            allowed = self._accepted[action]
            if current is None or current not in allowed:
                raise MissingUpcasterPath(
                    f"no-upcast-path:v{_version_label(version)}->v{target}",
                    f"{action} accepts versions {sorted(allowed)}, got {version!r}",
                )
            return target, self._chain(action, current, target)

        if current is None:
            raise MissingUpcasterPath(
                f"no-upcast-path:v{_version_label(version)}->v{target}",
                f"event version {version!r} is not an integer",
            )
        return target, self._chain(action, current, target)

    def _chain(self, action: str, current: int, target: int) -> List["UpcastStep"]:
        """Walk the registered chain ``current -> ... -> target``.

        A version newer than canonical has no chain to walk and is reported as
        a missing path rather than being folded with an unknown payload shape.
        """
        if current > target:
            raise MissingUpcasterPath(
                f"no-upcast-path:v{current}->v{target}",
                f"{action} v{current} is newer than the canonical v{target}",
            )
        steps: List[UpcastStep] = []
        seen = set()
        while current != target:
            if current in seen:
                raise MissingUpcasterPath(
                    f"no-upcast-path:v{current}->v{target}",
                    f"upcaster chain for {action} loops at v{current}",
                )
            seen.add(current)
            step = self._steps.get((action, current))
            if step is None:
                known = sorted(v for (a, v) in self._steps if a == action)
                raise MissingUpcasterPath(
                    f"no-upcast-path:v{current}->v{target}",
                    f"no upcaster registered for {action} v{current} "
                    f"(registered: {['v%d' % v for v in known]})",
                )
            steps.append(step)
            current = step.to_version
        return steps

    # -- migration ----------------------------------------------------------

    def migrate(
        self, action: str, version: Any, data: Any
    ) -> tuple:
        """Run the upcaster chain, returning ``(canonical, path, payload)``.

        ``path`` lists every version visited -- the origin followed by each
        upcaster target -- e.g. ``[1, 2, 3]`` for a v1 credit.
        """
        canonical, steps = self.resolve(action, version)
        payload: Mapping[str, Any] = dict(data) if isinstance(data, Mapping) else {}
        path = [_normalise_version(version)]
        for step in steps:
            payload = step.handler(payload)
            if not isinstance(payload, Mapping):
                raise MigrationError(
                    "invalid-upcast",
                    f"{action} v{step.from_version}->v{step.to_version} produced "
                    f"{type(payload).__name__}",
                )
            path.append(step.to_version)
        return canonical, path, dict(payload)


# -- default chains ---------------------------------------------------------


def _credit_v1_to_v2(data: Mapping[str, Any]) -> Dict[str, Any]:
    """v1 stored whole cents in ``data.amount``; v2 renamed it to ``cents``.

    v1 predates the ``currency`` field entirely, so migrated events are CNY.
    """
    return {"cents": _as_integer_cents(data.get("amount")), "currency": "CNY"}


def _credit_v2_to_v3(data: Mapping[str, Any]) -> Dict[str, Any]:
    """v2 -> v3: currency is validated, cents become decimal yuan strings.

    ``currency`` is mandatory in v2 and only CNY is accepted; an absent,
    blank or foreign currency rejects the event (it is never upcast to a
    silently assumed default).
    """
    currency = data.get("currency")
    if not isinstance(currency, str) or currency.strip() != "CNY":
        raise MigrationError(
            "unsupported-currency",
            f"currency {currency!r} is not accepted (only CNY)",
        )
    cents = _as_integer_cents(data.get("cents"))
    return {"amount": _cents_to_yuan(cents), "currency": "CNY"}


def build_default_registry() -> UpcasterRegistry:
    """Build (and freeze) the registry for the built-in event generations."""
    registry = UpcasterRegistry()
    for action in ("credit", "debit"):
        registry.register(action, 1, 2, _credit_v1_to_v2)
        registry.register(action, 2, 3, _credit_v2_to_v3)
    return registry.freeze()


#: Process wide, immutable registry shared by the default folding entry points.
DEFAULT_REGISTRY = build_default_registry()


# ---------------------------------------------------------------------------
# Replay result
# ---------------------------------------------------------------------------


@dataclass
class ReplayResult:
    """Outcome of folding a stream (or a prefix of one).

    The dataclass is deliberately plain data: it can be pickled, merged and
    resumed from one process to another.  ``state`` only ever reflects
    *migrated* events.
    """

    state: Dict[str, Any] = field(default_factory=_new_state)
    rejects: List[Dict[str, Any]] = field(default_factory=list)
    applied_by_version: Dict[str, int] = field(default_factory=dict)
    migrations: List[Dict[str, Any]] = field(default_factory=list)
    last_seq: Optional[int] = None
    applied: int = 0
    # Snapshot only: seq of the last applied rename, so a resumed fold can
    # rebuild its migration records without re-reading older events.
    name_seq: Optional[int] = None

    # -- construction -------------------------------------------------------

    @classmethod
    def empty(cls) -> "ReplayResult":
        return cls()

    @classmethod
    def from_snapshot(cls, snapshot: Optional[Mapping[str, Any]]) -> "ReplayResult":
        """Rebuild the fold cursor from a snapshot written by this module."""
        if not isinstance(snapshot, Mapping):
            return cls()
        result = cls(
            state=_normalise_state(snapshot.get("state")),
            rejects=copy_rejects(snapshot.get("rejects")),
            applied_by_version=copy_counts(snapshot.get("applied_by_version")),
            migrations=copy_migrations(snapshot.get("migrations")),
            name_seq=_as_plain_int(snapshot.get("name_seq")),
        )
        result.last_seq = _as_plain_int(snapshot.get("last_seq"))
        result.applied = int(result.state.get("applied", 0))
        return result

    # -- serialisation ------------------------------------------------------

    def to_output(self) -> Dict[str, Any]:
        return {
            "state": dict(self.state),
            "rejects": copy_rejects(self.rejects),
            "applied_by_version": copy_counts(self.applied_by_version),
            "migrations": copy_migrations(self.migrations),
        }

    def to_snapshot(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "last_seq": self.last_seq,
            "state": dict(self.state),
            "rejects": copy_rejects(self.rejects),
            "applied_by_version": copy_counts(self.applied_by_version),
            "migrations": copy_migrations(self.migrations),
            "name_seq": self.name_seq,
        }
        return payload

    def copy(self) -> "ReplayResult":
        return ReplayResult.from_snapshot(self.to_snapshot())


def copy_rejects(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, Mapping):
            out.append({"seq": item.get("seq"), "reason": item.get("reason")})
    return out


def copy_counts(counts: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not isinstance(counts, Mapping):
        return out
    for key, value in counts.items():
        number = _as_plain_int(value)
        if number is not None:
            out[str(key)] = number
    return out


def copy_migrations(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        out.append(
            {
                "seq": item.get("seq"),
                "type": item.get("type"),
                "path": [p for p in item.get("path", [])],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Engine: upcast + fold one event at a time
# ---------------------------------------------------------------------------


class ReplayEngine:
    """Re-entrant upcast-then-fold engine.

    Instances keep a fold cursor, but two engines never share it, so they can
    be used concurrently for different streams.
    """

    def __init__(self, registry: Optional[UpcasterRegistry] = None) -> None:
        self.registry = registry if registry is not None else DEFAULT_REGISTRY

    def fold_event(self, result: ReplayResult, event: Any) -> None:
        """Migrate and fold one event into ``result`` (mutates in place)."""
        seq = event.get("seq") if isinstance(event, Mapping) else None
        action = event.get("type") if isinstance(event, Mapping) else None
        version = event.get("v") if isinstance(event, Mapping) else None
        raw = event.get("data") if isinstance(event, Mapping) else None
        data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}

        # Remember how far the stream got, even if the event is rejected:
        # the snapshot must not replay (and re-reject) it again.
        result.last_seq = _to_seq(seq, result.last_seq)

        if action not in ACTION_TYPES:
            result.rejects.append({"seq": seq, "reason": "unknown-type"})
            return

        try:
            canonical, path, payload = self.registry.migrate(action, version, data)
            self._apply(result, action, canonical, payload, seq)
        except MigrationError as exc:
            result.rejects.append({"seq": seq, "reason": exc.reason})
            return

        # Only successfully migrated events are recorded/counted.
        result.migrations.append({"seq": seq, "type": action, "path": path})
        label = _version_label(version)
        result.applied_by_version[label] = result.applied_by_version.get(label, 0) + 1
        result.state["applied"] = int(result.state.get("applied", 0)) + 1
        result.applied += 1

    # -- fold one canonical event ------------------------------------------

    def _apply(
        self,
        result: ReplayResult,
        action: str,
        canonical: int,
        payload: Mapping[str, Any],
        seq: Any,
    ) -> None:
        if action in ("credit", "debit"):
            cents = _yuan_to_cents(payload.get("amount"))
            delta = cents if action == "credit" else -cents
            result.state["balance_cents"] = int(result.state.get("balance_cents", 0)) + delta
            return
        if action == "rename":
            name = payload.get("name")
            if not isinstance(name, str):
                raise MigrationError("invalid-name", f"expected a name string, got {name!r}")
            result.state["name"] = name
            # Pin the rename to its stream position; the cursor is a safe
            # fallback when the event carries no usable ``seq``.
            result.name_seq = _to_seq(seq, result.last_seq)
            return
        raise MigrationError("unknown-type", f"cannot apply {action!r}")

    def fold_events(
        self,
        events: Iterable[Any],
        result: Optional[ReplayResult] = None,
        *,
        fail_after: Optional[int] = None,
    ) -> ReplayResult:
        """Fold ``events`` into ``result`` and return it."""
        cursor = result if result is not None else ReplayResult()
        for index, event in enumerate(events):
            if fail_after is not None and index >= fail_after:
                raise ReplayInterrupted(cursor.copy(), index)
            self.fold_event(cursor, event)
        return cursor


def _to_seq(seq: Any, fallback: Optional[int]) -> Optional[int]:
    number = _as_plain_int(seq)
    if number is None:
        return fallback
    # Keep the cursor monotone: an out-of-order event must not make a later
    # snapshot skip events that have not been folded yet.
    return number if fallback is None else max(fallback, number)


# ---------------------------------------------------------------------------
# Ordering / merging
# ---------------------------------------------------------------------------


def _ordinal(value: Any) -> Tuple[int, float]:
    """Total order over ``seq`` values that tolerates missing/garbage seqs."""
    number = _as_plain_int(value)
    if number is not None:
        return (0, float(number))
    return (1, float("inf"))


def split_events(events: Sequence[Any]) -> Tuple[List[Any], List[Any]]:
    """Split a stream in half for the "two shards then merge" check."""
    middle = len(events) // 2
    return list(events[:middle]), list(events[middle:])


def merge_results(results: Iterable[ReplayResult]) -> ReplayResult:
    """Merge independently folded shards into one result.

    Ordering rules that make the merged result identical to a single fold:

    * ``rejects`` and ``migrations`` are stably sorted by ``seq``;
    * ``applied_by_version`` is a sum;
    * the final ``name`` is the one of the last *applied* rename, which is
      tracked via ``name_seq`` instead of blindly taking the last shard.

    Only shards folded from disjoint, ordered prefixes of one stream can be
    merged this way; the aggregate balance and ``applied`` count are additive
    and therefore order independent.
    """
    merged = ReplayResult()
    shards = [r for r in results if r is not None]
    if not shards:
        return merged

    for shard in shards:
        merged.state["balance_cents"] += int(shard.state.get("balance_cents", 0))
        merged.state["applied"] += int(shard.state.get("applied", 0))
        merged.rejects.extend(copy_rejects(shard.rejects))
        merged.migrations.extend(copy_migrations(shard.migrations))
        for key, value in copy_counts(shard.applied_by_version).items():
            merged.applied_by_version[key] = merged.applied_by_version.get(key, 0) + value
        if shard.last_seq is not None:
            if merged.last_seq is None or _ordinal(shard.last_seq) > _ordinal(merged.last_seq):
                merged.last_seq = shard.last_seq

    merged.rejects.sort(key=lambda item: _ordinal(item.get("seq")))
    merged.migrations.sort(key=lambda item: _ordinal(item.get("seq")))
    merged.applied = int(merged.state.get("applied", 0))

    # The last applied rename wins, independent of shard order.  ``name_seq``
    # pins the rename to its position in the stream.  A shard restored from a
    # legacy snapshot has no marker: its ``last_seq`` is the best available
    # bound, but only when it actually carries a name (otherwise an explicit
    # ``rename`` to "" in an earlier shard would be overridden).
    winner = None
    winner_seq = None
    for shard in shards:
        if shard.name_seq is not None:
            seq = shard.name_seq
        elif shard.state.get("name") and shard.last_seq is not None:
            seq = shard.last_seq
        else:
            continue
        if winner_seq is None or _ordinal(seq) >= _ordinal(winner_seq):
            winner_seq = seq
            winner = shard.state.get("name", "")
    if winner is not None:
        merged.state["name"] = winner
    merged.name_seq = winner_seq
    return merged


def fold_shards(
    shards: Sequence[Sequence[Any]],
    *,
    registry: Optional[UpcasterRegistry] = None,
    max_workers: Optional[int] = None,
) -> ReplayResult:
    """Fold disjoint shards concurrently and merge them in stream order."""
    engine = ReplayEngine(registry)
    shard_lists = [list(shard) for shard in shards]
    if len(shard_lists) <= 1:
        return engine.fold_events(shard_lists[0] if shard_lists else [])
    workers = max_workers or len(shard_lists)
    with ThreadPoolExecutor(max_workers=min(workers, len(shard_lists))) as pool:
        futures = [pool.submit(engine.fold_events, shard) for shard in shard_lists]
        results = [future.result() for future in futures]
    return merge_results(results)


def fold_events(
    events: Iterable[Any],
    *,
    initial: Optional[ReplayResult] = None,
    registry: Optional[UpcasterRegistry] = None,
    fail_after: Optional[int] = None,
) -> ReplayResult:
    """Convenience wrapper: ``ReplayEngine(registry).fold_events(...)``."""
    return ReplayEngine(registry).fold_events(events, initial, fail_after=fail_after)


def iter_replay_records(result: ReplayResult) -> Iterator[Dict[str, Any]]:
    """Yield per-event migration records in fold order."""
    for record in copy_migrations(result.migrations):
        yield record


def resume_from_snapshot(
    events: Sequence[Any],
    snapshot: Optional[Mapping[str, Any]],
    *,
    registry: Optional[UpcasterRegistry] = None,
) -> Tuple[ReplayResult, List[Any]]:
    """Rehydrate a fold cursor from ``snapshot`` and return ``(result, todo)``.

    The cached state is taken verbatim from the snapshot -- it was produced by
    folding migrated events only.  A snapshot written by an older tool may lack
    the per-event records (or the ``name_seq`` marker); those are rebuilt from
    the covered prefix, which is safe because folding is deterministic.  The
    rebuilt records are therefore always identical to the ones a full fold
    would produce.
    """
    initial = ReplayResult.from_snapshot(snapshot)
    last_seq = initial.last_seq
    if last_seq is None:
        return initial, list(events)

    prefix = [event for event in events if not _is_after(event.get("seq"), last_seq)]
    todo = [event for event in events if _is_after(event.get("seq"), last_seq)]

    missing_records = not isinstance(snapshot, Mapping) or "migrations" not in snapshot
    missing_marker = not isinstance(snapshot, Mapping) or "name_seq" not in snapshot
    if missing_records or missing_marker:
        rebuilt = fold_events(prefix, registry=registry)
        if missing_records:
            initial.rejects = rebuilt.rejects
            initial.applied_by_version = rebuilt.applied_by_version
            initial.migrations = rebuilt.migrations
        if missing_marker:
            initial.name_seq = rebuilt.name_seq
    return initial, todo


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_events(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, Mapping):
        events = payload.get("events", [])
    else:
        events = payload
    if not isinstance(events, list):
        raise ValueError("events.json must contain an 'events' array")
    return events


def load_snapshot(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except FileNotFoundError:
        return None
    return snapshot if isinstance(snapshot, Mapping) else None


def write_json(path: str, payload: Any) -> None:
    """Atomically replace ``path`` with ``payload`` (crash-safe snapshots)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".tmp-", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("events", help="input event stream JSON")
    parser.add_argument("output", help="output result JSON")
    parser.add_argument("--snapshot", dest="snapshot", help="snapshot file to resume from and update")
    parser.add_argument("--fail-after", dest="fail_after", type=int, help="abort with exit 3 after N events")
    parser.add_argument(
        "--shards",
        dest="shards",
        type=int,
        default=0,
        help="fold the stream in N parallel shards and merge (self-check)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        events = load_events(args.events)
    except (OSError, ValueError) as exc:
        print(f"replay: cannot read events: {exc}", file=sys.stderr)
        return 2

    snapshot = load_snapshot(args.snapshot)
    initial, todo = resume_from_snapshot(events, snapshot)

    try:
        if args.shards and args.shards > 1:
            # Shards must be contiguous ranges of the ordered stream: the fold
            # is a left fold, so only ordered prefixes can be merged back.
            chunks = _contiguous_chunks(todo, args.shards)
            partial = fold_shards(chunks, max_workers=args.shards)
            result = merge_results([initial, partial])
        elif args.fail_after is not None:
            try:
                result = fold_events(todo, initial=initial, fail_after=args.fail_after)
            except ReplayInterrupted as stop:
                if args.snapshot:
                    write_json(args.snapshot, stop.result.to_snapshot())
                print(
                    f"replay: interrupted after {stop.processed} event(s); "
                    f"snapshot committed at seq={stop.result.last_seq}",
                    file=sys.stderr,
                )
                return 3
        else:
            result = fold_events(todo, initial=initial)
    except MissingUpcasterPath as exc:  # pragma: no cover - defensive
        print(f"replay: {exc}", file=sys.stderr)
        return 2

    write_json(args.output, result.to_output())
    if args.snapshot:
        write_json(args.snapshot, result.to_snapshot())
    return 0


def _is_after(seq: Any, last_seq: Optional[int]) -> bool:
    ordinal = _as_plain_int(seq)
    if ordinal is None:
        return True
    return last_seq is None or ordinal > last_seq


def _contiguous_chunks(events: Sequence[Any], count: int) -> List[List[Any]]:
    size, remainder = divmod(len(events), count)
    chunks: List[List[Any]] = []
    start = 0
    for index in range(count):
        width = size + (1 if index < remainder else 0)
        chunks.append(list(events[start : start + width]))
        start += width
    return chunks


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
