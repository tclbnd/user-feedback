#!/usr/bin/env python3
"""Bitemporal association of facts with slowly changing dimensions.

Two independent time axes are kept for every dimension row:

* business time -- the half-open interval ``[from, to)`` (``to`` null means
  +infinity).  Splitting a row is simply two new rows; re-claiming a
  previously removed range and revoking a range are ordinary events.
* system time -- ``event.system``, the moment the row became known.  The
  effective system interval of a row starts at its own system time and is
  narrowed whenever a later ``(system, version)`` event covering the same
  business instant arrives.  Events are never mutated in place, so an old
  dimension version stays retrievable and old reports remain explainable.

Facts carry a business instant ``at``.  A fact is matched against the
covering dimension event with the greatest ``(system, version)`` pair that
was known at the report's ``asof``.  ``op=remove`` shadows the range, so
the fact reports ``null`` with ``reason=removed``; an uncovered instant
reports ``reason=no_dimension``.

Events are totally ordered by ``(system, version)`` because ``version`` is a
globally unique, non-empty event identifier.  That order is stable across
batches, partitions and replays.

Commands:
    python3 bitemporal.py apply     state.db events.json [--details]
    python3 bitemporal.py query     state.db --asof S      # recompute a report
    python3 bitemporal.py publish   state.db --asof S      # issued report, pinned
    python3 bitemporal.py dimensions state.db --asof S --at T
    python3 bitemporal.py fact      state.db ID --asof S
    python3 bitemporal.py unmatched state.db --asof S
    python3 bitemporal.py history   state.db [--asof S]
    python3 bitemporal.py log       state.db
    python3 bitemporal.py verify    state.db

``query`` recomputes from the immutable log and therefore reflects later
backfills; ``publish`` returns what was actually issued.  Both are needed:
recomputation is authoritative for today, the pinned report is what keeps a
past report explainable.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

STATE_FORMAT = 2


class InputError(Exception):
    """Malformed input or state; the CLI turns it into exit status 2."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def canon(value):
    """Canonical JSON used for duplicate/conflict detection."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_key(event):
    """Stable total replay order: (system, version)."""
    return (event["system"], event["version"])


def sort_ids(ids):
    values = list(ids)
    try:
        return sorted(values)
    except TypeError:
        return sorted(values, key=lambda value: (type(value).__name__, str(value)))


def _require(event, key, kind=None):
    if key not in event:
        raise InputError("event is missing %r: %s" % (key, canon(event)))
    value = event[key]
    if kind == "int" and not _is_int(value):
        raise InputError("%r must be an integer: %s" % (key, canon(event)))
    if kind == "scalar" and not (isinstance(value, str) or _is_int(value)):
        raise InputError("%r must be a string or integer: %s" % (key, canon(event)))
    return value


def validate_event(event):
    if not isinstance(event, dict):
        raise InputError("event must be an object: %r" % (event,))

    system = _require(event, "system", "int")
    if system < 0:
        raise InputError("system must be non-negative: %s" % canon(event))

    version = event.get("version")
    if not isinstance(version, str) or version == "":
        raise InputError("version must be a non-empty string: %s" % canon(event))

    kind = event.get("type")
    if kind == "fact":
        _require(event, "id", "scalar")
        _require(event, "entity", "scalar")
        _require(event, "at", "int")
        if event.get("op", "upsert") not in ("upsert", "delete"):
            raise InputError("invalid fact op: %s" % canon(event))
    elif kind == "dimension":
        _require(event, "entity", "scalar")
        start = _require(event, "from", "int")
        if "to" not in event:
            raise InputError("dimension is missing 'to': %s" % canon(event))
        end = event["to"]
        if end is not None and not _is_int(end):
            raise InputError("'to' must be an integer or null: %s" % canon(event))
        if end is not None and end < start:
            raise InputError("'to' must not precede 'from': %s" % canon(event))
        if event.get("op", "upsert") not in ("upsert", "remove"):
            raise InputError("invalid dimension op: %s" % canon(event))
    else:
        raise InputError("invalid event type: %s" % canon(event))


def fact_upserted(event):
    return event.get("op", "upsert") != "delete"


def dimension_upserted(event):
    return event.get("op", "upsert") != "remove"


def covers(event, at):
    if at < event["from"]:
        return False
    end = event["to"]
    return end is None or at < end


def pick_fact(events, asof):
    """Winning fact event for one id at a system time."""
    best = None
    best_key = None
    for event in events:
        if event["system"] > asof:
            continue
        key = event_key(event)
        if best_key is None or key > best_key:
            best, best_key = event, key
    return best


def pick_dimension(events, at, asof):
    """Winning dimension event for one entity at business instant ``at``."""
    best = None
    best_key = None
    for event in events:
        if event["system"] > asof or not covers(event, at):
            continue
        key = event_key(event)
        if best_key is None or key > best_key:
            best, best_key = event, key
    return best


def make_row(fact_id, fact, dimension):
    row = {
        "id": fact_id,
        "fact_version": fact["version"],
        "entity": fact["entity"],
        "at": fact["at"],
        "dimension_version": None,
        "label": None,
        "reason": "no_dimension",
    }
    if dimension is not None:
        if dimension_upserted(dimension):
            row["reason"] = "matched"
            row["dimension_version"] = dimension["version"]
            row["label"] = dimension.get("label")
        else:
            row["reason"] = "removed"
    return row


def covering_candidates(events, at, asof):
    """All dimension versions that cover ``at`` and are known at ``asof``.

    Returned in replay order.  This is the audit trail behind an unmatched
    row: for ``removed`` the last entry is the shadowing remove, and for
    ``no_dimension`` the list is empty.
    """
    found = [
        event
        for event in events
        if event["system"] <= asof and covers(event, at)
    ]
    found.sort(key=event_key)
    return [
        {
            "version": event["version"],
            "op": event.get("op", "upsert"),
            "from": event["from"],
            "to": event["to"],
            "system": event["system"],
            "label": event.get("label"),
        }
        for event in found
    ]


def index_events(events):
    facts = {}
    dimensions = {}
    for event in events:
        if event["type"] == "fact":
            facts.setdefault(event["id"], []).append(event)
        else:
            dimensions.setdefault(event["entity"], []).append(event)
    return facts, dimensions


def report_rows(fact_index, dim_index, asof):
    rows = []
    for fact_id in sort_ids(fact_index):
        fact = pick_fact(fact_index[fact_id], asof)
        if fact is None or not fact_upserted(fact):
            continue
        dimension = pick_dimension(dim_index.get(fact["entity"], []), fact["at"], asof)
        rows.append(make_row(fact_id, fact, dimension))
    return rows


def report(events, asof):
    """Independent from-scratch report as of a system instant."""
    fact_index, dim_index = index_events(events)
    return {"asof": asof, "rows": report_rows(fact_index, dim_index, asof)}


def report_digest(rows):
    body = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# explicit bitemporal dimension table

def dimension_history(dim_index):
    """The explicit bitemporal dimension table: two intervals per row.

    Every returned row states, for one entity,

    * ``[business_from, business_to)`` -- the business range, and
    * ``[system_from, system_to)`` -- the system range (``None`` = open ended)

    such that inside the rectangle the row's version is *the* answer a report
    computed at that system time would have used.  Rows are exact, not
    approximate: an earlier revision that a later revision only partly
    overlapped keeps the part of its business range it still owns, which is
    why a single update can produce several output rows (a split).

    Construction: cut business time at every declared boundary, then on each
    atomic business strip walk the covering revisions in ``(system, version)``
    order.  A revision is authoritative from its own system time until the
    next greater key covering the same strip becomes known.  Two revisions at
    the same system time therefore yield one row with an empty system
    interval -- the ``shadowed`` loser -- instead of an ambiguity.

    Business strips no revision covers are omitted here; they surface as
    ``no_dimension``/``removed`` in the ``fact`` and ``dimensions`` queries,
    which also list the covering candidates that explain the outcome.
    """
    rows = []
    for entity in sort_ids(dim_index):
        events = sorted(dim_index[entity], key=event_key)
        strips = business_strips(events)
        for strip_from, strip_to in strips:
            covering = [event for event in events if ranges_overlap_range(event, strip_from, strip_to)]
            for position, event in enumerate(covering):
                later = covering[position + 1 :]
                system_to = min((other["system"] for other in later), default=None)
                rows.append(
                    {
                        "entity": entity,
                        "version": event["version"],
                        "op": event.get("op", "upsert"),
                        "label": event.get("label"),
                        "business_from": strip_from,
                        "business_to": strip_to,
                        "system_from": event["system"],
                        "system_to": system_to,
                        "shadowed": system_to is not None
                        and system_to == event["system"],
                    }
                )
    return merge_adjacent(rows)


def business_strips(events):
    """Atomic business intervals induced by all declared boundaries."""
    points = set()
    for event in events:
        points.add(event["from"])
        if event["to"] is not None:
            points.add(event["to"])
    ordered = sorted(points)
    strips = []
    for index, start in enumerate(ordered):
        end = ordered[index + 1] if index + 1 < len(ordered) else None
        strips.append((start, end))
    return strips


def ranges_overlap_range(event, start, end):
    """Does the event's business interval cover the whole strip ``[start, end)``?"""
    if event["from"] > start:
        return False
    event_end = event["to"]
    if end is None:
        return event_end is None
    return event_end is None or event_end >= end


def merge_adjacent(rows):
    """Collapse contiguous strips that carry an identical authority row."""
    order = sorted(
        rows,
        key=lambda row: (
            str(row["entity"]),
            row["version"],
            row["system_from"],
            float("-inf") if row["system_to"] is None else row["system_to"],
            row["business_from"],
        ),
    )
    merged = []
    for row in order:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous["entity"] == row["entity"]
            and previous["version"] == row["version"]
            and previous["system_from"] == row["system_from"]
            and previous["system_to"] == row["system_to"]
            and previous["op"] == row["op"]
            and previous["label"] == row["label"]
            and previous["shadowed"] == row["shadowed"]
            and previous["business_to"] == row["business_from"]
        ):
            previous["business_to"] = row["business_to"]
            continue
        merged.append(dict(row))
    merged.sort(
        key=lambda row: (str(row["entity"]), row["system_from"], row["business_from"])
    )
    return merged


def unmatched_reasons(dim_index, asof, ats):
    """Why a business instant has no usable dimension, at one system time.

    * ``no_dimension`` -- no revision at all covers the instant;
    * ``removed``      -- the newest covering revision is a removal.

    The covering candidates are included so an old report's join can be
    explained without trusting the current view.
    """
    result = {}
    for entity in sort_ids(dim_index):
        for at in sort_ids(ats):
            winner = pick_dimension(dim_index[entity], at, asof)
            result.setdefault(entity, {})[at] = {
                "reason": "no_dimension"
                if winner is None
                else ("matched" if dimension_upserted(winner) else "removed"),
                "dimension_version": None if winner is None else winner["version"],
                "candidates": covering_candidates(dim_index[entity], at, asof),
            }
    return result


def query_unmatched(state_path, asof):
    """Every business instant present in the facts, explained at ``asof``.

    This is the reproducibility aid for an old report: for each entity and
    each business instant a fact actually lands on, it reports the chosen
    version, the reason when there is none (``no_dimension`` / ``removed``)
    and the full ordered candidate list that produced that answer.
    """
    state = load_state(state_path)
    ats = set()
    for fact_events in state["fact_events"].values():
        for event in fact_events:
            ats.add(event["at"])
    reasons = unmatched_reasons(state["dimension_events"], asof, ats)
    rows = []
    for entity in sort_ids(reasons):
        for at in sort_ids(reasons[entity]):
            rows.append(dict(reasons[entity][at], entity=entity, at=at))
    return {"asof": asof, "rows": rows}


# ---------------------------------------------------------------------------
# state

def empty_state():
    return {
        "format": STATE_FORMAT,
        "events": [],
        "max_system": None,
        "current": {"asof": None, "rows": []},
        "log": [],
    }


def load_json(path, what):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError("cannot read %s %s: %s" % (what, path, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InputError("invalid JSON in %s %s: %s" % (what, path, exc))


def normalize_events(raw_events):
    """Validate, de-duplicate by version and sort into replay order."""
    if not isinstance(raw_events, list):
        raise InputError("events must be a list")

    seen = {}
    unique = []
    for event in raw_events:
        validate_event(event)
        payload = canon(event)
        version = event["version"]
        previous = seen.get(version)
        if previous is not None:
            if previous != payload:
                raise InputError("conflicting payloads for version %r" % version)
            continue
        seen[version] = payload
        unique.append(event)

    unique.sort(key=event_key)
    return unique


def load_state(path):
    """Load persisted state, upgrading the original prototype format.

    The event log is the source of truth.  The materialized current view
    is reused when it is stamped with the current maximum system time;
    otherwise it is rebuilt from the log.
    """
    if not path.exists():
        state = empty_state()
        state["fact_events"], state["dimension_events"] = {}, {}
        return state

    data = load_json(path, "state")
    if not isinstance(data, dict):
        raise InputError("state must be a JSON object")

    events = normalize_events(data.get("events", []))
    max_system = None
    for event in events:
        if max_system is None or event["system"] > max_system:
            max_system = event["system"]

    state = empty_state()
    state["events"] = events
    state["max_system"] = max_system
    state["fact_events"], state["dimension_events"] = index_events(events)

    log = data.get("log", [])
    if not isinstance(log, list):
        raise InputError("state log must be a list")
    state["log"] = log

    current = data.get("current")
    if (
        isinstance(current, dict)
        and current.get("asof") == max_system
        and isinstance(current.get("rows"), list)
    ):
        rows = current["rows"]
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or "id" not in row:
                raise InputError("invalid current view row: %r" % (row,))
            if row["id"] in seen:
                raise InputError("duplicate id in current view: %r" % (row["id"],))
            seen.add(row["id"])
        state["current"] = {"asof": max_system, "rows": rows}
    else:
        state["current"] = report(events, max_system)

    return state


def atomic_write(path, text):
    directory = str(path.parent) if str(path.parent) else "."
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def save_state(path, state):
    payload = {
        "format": STATE_FORMAT,
        "events": state["events"],
        "max_system": state["max_system"],
        "current": state["current"],
        "log": state["log"],
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write(path, text)


# ---------------------------------------------------------------------------
# apply

def apply_batch(state_path, input_path, include_details=False):
    state = load_state(state_path)
    payload = load_json(input_path, "input")
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise InputError("input must be an object with an events list")

    # Duplicate/conflict detection against both the persisted log and the
    # batch itself, so a replay is idempotent no matter how it is split
    # into files or partitions.
    known = {event["version"]: canon(event) for event in state["events"]}
    accepted_events = []
    batch_seen = {}
    for event in payload["events"]:
        validate_event(event)
        body = canon(event)
        version = event["version"]
        if version in batch_seen:
            if batch_seen[version] != body:
                raise InputError("conflicting payloads for version %r (batch)" % version)
            continue
        if version in known:
            if known[version] != body:
                raise InputError(
                    "conflicting payloads for version %r (state)" % version
                )
            continue
        batch_seen[version] = body
        accepted_events.append(event)

    accepted_events.sort(key=event_key)
    accepted = len(accepted_events)

    old_rows = {row["id"]: row for row in state["current"]["rows"]}
    old_max = state["max_system"]

    if accepted == 0:
        # A pure replay is a no-op and must not move the report's clock.
        if not state_path.exists():
            save_state(state_path, state)
        result = {"accepted": 0, "affected": []}
        if include_details:
            result["changes"] = []
        return result

    events = state["events"] + accepted_events
    events.sort(key=event_key)
    fact_index, dim_index = index_events(events)

    new_max = old_max
    for event in accepted_events:
        if new_max is None or event["system"] > new_max:
            new_max = event["system"]

    # Fact ids whose selected fact version (or its deletion) moves because
    # of this batch.
    new_fact_ids = {e["id"] for e in accepted_events if e["type"] == "fact"}
    changed_fact_ids = set()
    for fact_id in new_fact_ids:
        before = pick_fact(state["fact_events"].get(fact_id, []), old_max)
        after = pick_fact(fact_index.get(fact_id, []), new_max)
        before_key = None if before is None else event_key(before)
        after_key = None if after is None else event_key(after)
        if before_key != after_key:
            changed_fact_ids.add(fact_id)

    # Effective facts after the batch, used to evaluate the business-time
    # scope of newly accepted dimension events.
    live_facts = {}
    for fact_id, row in old_rows.items():
        live_facts[fact_id] = (row["entity"], row["at"])
    for fact_id in new_fact_ids:
        fact = pick_fact(fact_index.get(fact_id, []), new_max)
        if fact is None or not fact_upserted(fact):
            live_facts.pop(fact_id, None)
        else:
            live_facts[fact_id] = (fact["entity"], fact["at"])

    covered_fact_ids = set()
    for event in accepted_events:
        if event["type"] != "dimension":
            continue
        start = event["from"]
        end = event["to"]
        for fact_id, (entity, at) in live_facts.items():
            if entity != event["entity"] or at < start:
                continue
            if end is not None and at >= end:
                continue
            covered_fact_ids.add(fact_id)

    # A fact is in scope when its own selected version moved (including a
    # delete or restore) or when an accepted dimension event of its entity
    # covers the fact's business instant.  A deleted fact is still listed:
    # its association outcome changed even though it no longer yields a row.
    affected = changed_fact_ids | covered_fact_ids

    # Incremental current view: untouched rows are carried over verbatim,
    # facts deleted by this batch are dropped, live affected keys are
    # rematched.  Rows outside the impact scope are never touched.
    new_rows = dict(old_rows)
    for fact_id in new_fact_ids:
        if fact_id not in live_facts:
            new_rows.pop(fact_id, None)
    for fact_id in affected & set(live_facts):
        fact = pick_fact(fact_index[fact_id], new_max)
        dimension = pick_dimension(dim_index.get(fact["entity"], []), fact["at"], new_max)
        new_rows[fact_id] = make_row(fact_id, fact, dimension)

    # Change log.  Every rewritten key is listed exactly once, including
    # keys whose content happens to stay identical (a shadowed revision).
    changes = []
    for fact_id in sort_ids(affected):
        before = old_rows.get(fact_id)
        after = new_rows.get(fact_id)
        if before != after:
            changes.append({"id": fact_id, "before": before, "after": after})

    # Rows are the report for the new maximum system time.  A fact's own
    # business instant never moves into the future relative to its system
    # time, but historical reports are still produced from the event log.
    state["events"] = events
    state["max_system"] = new_max
    state["fact_events"], state["dimension_events"] = fact_index, dim_index
    state["current"] = {
        "asof": new_max,
        "rows": [new_rows[fact_id] for fact_id in sort_ids(new_rows)],
    }

    entry = {
        "batch": len(state["log"]) + 1,
        "accepted": accepted,
        "affected": sort_ids(affected),
        "changes": changes,
        "accepted_versions": [event["version"] for event in accepted_events],
        # The published report is pinned verbatim.  A backfill with an old
        # system time legitimately changes what today's recomputation of the
        # same as-of time yields, but the report that was actually issued
        # must stay byte-identical to be explainable.  Pinning the rows also
        # pins the fact and dimension versions that produced them.
        "published": {
            "asof": new_max,
            "fact_count": len(state["current"]["rows"]),
            "digest": report_digest(state["current"]["rows"]),
            "rows": state["current"]["rows"],
        },
    }
    state["log"].append(entry)
    save_state(state_path, state)

    result = {"accepted": accepted, "affected": sort_ids(affected)}
    if include_details:
        result["changes"] = changes
    return result


# ---------------------------------------------------------------------------
# queries

def query_report(state_path, asof):
    state = load_state(state_path)
    return report(state["events"], asof)


def query_fact(state_path, fact_id, asof):
    """Full provenance for one fact: chosen versions and why."""
    state = load_state(state_path)
    fact_events = state["fact_events"].get(fact_id, [])
    fact = pick_fact(fact_events, asof)
    if fact is None:
        return {
            "id": fact_id,
            "asof": asof,
            "row": None,
            "fact_candidates": [],
            "dimension_candidates": [],
        }
    dimension_events = state["dimension_events"].get(fact["entity"], [])
    dimension = pick_dimension(dimension_events, fact["at"], asof)
    return {
        "id": fact_id,
        "asof": asof,
        "row": make_row(fact_id, fact, dimension),
        "fact_candidates": [
            {"version": e["version"], "system": e["system"], "at": e["at"],
             "entity": e["entity"], "op": e.get("op", "upsert")}
            for e in sorted(fact_events, key=event_key)
            if e["system"] <= asof
        ],
        "dimension_candidates": covering_candidates(dimension_events, fact["at"], asof),
    }


def query_business(state_path, asof, at):
    """Dimension versions believed at ``asof`` to apply to business instant ``at``.

    This is the second query axis: a historical report is reproducible by
    asking for the dimension presumed at system time S for each business
    instant T.
    """
    state = load_state(state_path)
    entities = set(state["dimension_events"])
    for fact_events in state["fact_events"].values():
        fact = pick_fact(fact_events, asof)
        if fact is not None:
            entities.add(fact["entity"])
    rows = []
    for entity in sort_ids(entities):
        events = state["dimension_events"].get(entity, [])
        event = pick_dimension(events, at, asof)
        rows.append(
            {
                "entity": entity,
                "dimension_version": None if event is None else event["version"],
                "label": None if event is None else event.get("label"),
                "from": None if event is None else event["from"],
                "to": None if event is None else event["to"],
                "reason": (
                    "no_dimension"
                    if event is None
                    else ("matched" if dimension_upserted(event) else "removed")
                ),
                "candidates": covering_candidates(events, at, asof),
            }
        )
    return {"asof": asof, "at": at, "rows": rows}


def query_published(state_path, asof=None, batch=None, latest=False):
    """Return a pinned (issued) report instead of a recomputation.

    A late backfill can change what today's recomputation for an as-of time
    yields.  The ``query`` command always *recomputes*; this command returns
    the rows exactly as they were issued.

    When several batches published the same as-of time (possible only when a
    backfill with an already-known system time arrives), the *earliest* is
    returned by default because that is the report that was originally
    issued; pass ``latest=True`` for the most recent publication.
    """
    state = load_state(state_path)
    entries = state["log"]
    if batch is not None:
        matches = [entry for entry in entries if entry.get("batch") == batch]
    elif asof is not None:
        matches = [
            entry
            for entry in entries
            if isinstance(entry.get("published"), dict)
            and entry["published"].get("asof") == asof
        ]
    else:
        raise InputError("publish requires --asof or --batch")
    if not matches:
        raise InputError("no published report matches that selector")
    entry = matches[-1] if latest else matches[0]
    published = entry["published"]
    return {
        "batch": entry["batch"],
        "asof": published["asof"],
        "digest": published["digest"],
        "rows": published["rows"],
    }


def query_log(state_path):
    return {"batches": load_state(state_path)["log"]}


def query_history(state_path, asof=None):
    """The explicit bitemporal dimension table.

    With ``asof`` given, only revisions whose system interval contains that
    instant (``system_from <= asof < system_to``) are returned, which is the
    set of dimension versions a report computed at ``asof`` could have used.
    """
    state = load_state(state_path)
    rows = dimension_history(state["dimension_events"])
    if asof is None:
        return {"rows": rows}
    visible = [
        row
        for row in rows
        if row["system_from"] <= asof
        and (row["system_to"] is None or asof < row["system_to"])
    ]
    return {"asof": asof, "rows": visible}


def verify_state(state_path):
    """Check the materialized view and every published report digest.

    There are two distinct guarantees and both are checked here:

    * the pinned rows of every published report still hash to their digest
      (the issued report is intact, even if later backfills would now
      recompute that as-of time differently);
    * the materialized current view equals a from-scratch recomputation
      from the immutable log.
    """
    state = load_state(state_path)
    problems = []

    rebuilt = report(state["events"], state["max_system"])
    materialized = {
        "asof": state["current"]["asof"],
        "rows": state["current"]["rows"],
    }
    if rebuilt != materialized:
        problems.append(
            {
                "kind": "current_view_mismatch",
                "expected_digest": report_digest(rebuilt["rows"]),
                "actual_digest": report_digest(materialized["rows"]),
            }
        )

    reproduced = []
    for entry in state["log"]:
        published = entry.get("published")
        if not isinstance(published, dict) or "asof" not in published:
            problems.append({"kind": "log_entry_without_digest", "batch": entry.get("batch")})
            continue
        if report_digest(published.get("rows", [])) != published.get("digest"):
            problems.append(
                {"kind": "published_rows_corrupted", "batch": entry.get("batch")}
            )
            continue
        now = report_digest(report(state["events"], published["asof"])["rows"])
        reproduced.append(
            {
                "batch": entry["batch"],
                "asof": published["asof"],
                "pinned_digest": published["digest"],
                "recomputed_digest": now,
                "backfilled": now != published["digest"],
            }
        )

    return {
        "ok": not problems,
        "events": len(state["events"]),
        "max_system": state["max_system"],
        "batches": len(state["log"]),
        "published": reproduced,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# CLI

def emit(value):
    sys.stdout.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def build_parser():
    parser = argparse.ArgumentParser(description="Bitemporal fact/dimension association")
    sub = parser.add_subparsers(dest="op", required=True)

    apply_parser = sub.add_parser("apply", help="apply one batch of events")
    apply_parser.add_argument("state", type=Path)
    apply_parser.add_argument("input", type=Path)
    apply_parser.add_argument(
        "--details", action="store_true", help="also print per-key change details"
    )

    query_parser = sub.add_parser("query", help="report as of a system time")
    query_parser.add_argument("state", type=Path)
    query_parser.add_argument("--asof", type=int, required=True)

    report_parser = sub.add_parser(
        "dimensions", help="dimension versions for a (system, business) pair"
    )
    report_parser.add_argument("state", type=Path)
    report_parser.add_argument("--asof", type=int, required=True)
    report_parser.add_argument("--at", type=int, required=True)

    fact_parser = sub.add_parser("fact", help="provenance of one fact")
    fact_parser.add_argument("state", type=Path)
    fact_parser.add_argument("id")
    fact_parser.add_argument("--asof", type=int, required=True)

    unmatched_parser = sub.add_parser(
        "unmatched", help="explain every fact business instant at one system time"
    )
    unmatched_parser.add_argument("state", type=Path)
    unmatched_parser.add_argument("--asof", type=int, required=True)

    log_parser = sub.add_parser("log", help="show the apply change log")
    log_parser.add_argument("state", type=Path)

    verify_parser = sub.add_parser(
        "verify", help="re-check the current view and all published reports"
    )
    verify_parser.add_argument("state", type=Path)

    published_parser = sub.add_parser(
        "publish", help="show an issued report exactly as it was published"
    )
    published_parser.add_argument("state", type=Path)
    selector = published_parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--asof", type=int)
    selector.add_argument("--batch", type=int)
    published_parser.add_argument(
        "--latest",
        action="store_true",
        help="pick the most recent publication of that as-of time",
    )

    history_parser = sub.add_parser(
        "history", help="explicit bitemporal dimension table"
    )
    history_parser.add_argument("state", type=Path)
    history_parser.add_argument("--asof", type=int)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.op == "apply":
            result = apply_batch(args.state, args.input, include_details=args.details)
        elif args.op == "query":
            result = query_report(args.state, args.asof)
        elif args.op == "dimensions":
            result = query_business(args.state, args.asof, args.at)
        elif args.op == "fact":
            result = query_fact(args.state, args.id, args.asof)
        elif args.op == "unmatched":
            result = query_unmatched(args.state, args.asof)
        elif args.op == "verify":
            result = verify_state(args.state)
        elif args.op == "history":
            result = query_history(args.state, args.asof)
        elif args.op == "publish":
            result = query_published(
                args.state, asof=args.asof, batch=args.batch, latest=args.latest
            )
        else:
            result = query_log(args.state)
    except InputError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    emit(result)
    return 0 if not (args.op == "verify" and not result["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
