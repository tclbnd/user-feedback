#!/usr/bin/env python3
"""Normalize captured ledgers from several internal sources.

    python3 crawl.py sim.json output.json [--state PATH] [--fail-at POINT]

Rows are identified by (source, partition, seq); the mapping used to read a
row is chosen by (source, schema) and must be approved.  Rows that cannot be
interpreted are quarantined with their original values, the offending column
and the reason, and an approval over the matching sequence interval may
backfill them later.  Business keys accept only strictly newer revisions, so
late or backfilled records never resurrect deleted keys or overwrite newer
values.  Ledger, quarantine, conflicts, cursors, backfill list and audit are
committed together, and the accepted events are journaled so that
interruptions and re-chunked replays produce identical results.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

STATE_VERSION = 3
SEP = "\x1f"                  # separator for composite keys kept inside state
MISSING = object()

ROW_OPS = ("upsert", "delete")


# ---------------------------------------------------------------------------
# helpers

def canonical(value):
    """Canonical compact JSON used for content comparisons and digests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def event_digest(event):
    """Content fingerprint of an input event (the id is not content)."""
    payload = {key: value for key, value in event.items() if key != "id"}
    return sha256_hex(canonical(payload))


def rule_digests(rule):
    """Accept the compact-JSON digest in ASCII-escaped or raw UTF-8 form."""
    digests = set()
    for ensure_ascii in (True, False):
        blob = json.dumps(
            rule, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii
        )
        digests.add(sha256_hex(blob))
    return digests


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def business_key(source, partition, key):
    return SEP.join((source, partition, str(key)))


def capture_key(source, partition, seq):
    return SEP.join((source, partition, str(seq)))


def mapping_key(source, schema):
    return SEP.join((source, str(schema)))


def resolve_path(value):
    """Resolve a state/output path from the command line (None stays None)."""
    return None if value is None else Path(value).expanduser()


# ---------------------------------------------------------------------------
# persistent state: an append-only journal
#
# One line per committed event, each line flushed and fsynced separately.  A
# line is a commit record only once it is complete, so recovery drops a torn
# trailing line: the checkpoint on disk is always a whole number of commits,
# and each commit carries the ledger, quarantine, conflicts, cursors, backfill
# list, audit, dedupe marker and accepted log together (they are all derived
# from the same journal).

def new_state(path=None):
    return {
        "version": STATE_VERSION,
        "path": path,
        "header_seen": False,
        "base_rules": {},      # initial (already effective) rules by source/schema
        "events": {},          # str(event id) -> accepted event
        "derived": None,       # in-memory derived view (never persisted)
    }


def read_journal(path):
    """Return (header_seen, base_rules, events), ignoring a torn tail.

    A record counts only once its terminating newline is present, so a
    partially written final append is not mistaken for a commit.
    """
    base_rules, events, header_seen = {}, {}, False
    if path is None or not path.exists():
        return header_seen, base_rules, events
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.endswith("\n"):
                break                      # truncated final append: not committed
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                break                      # torn/corrupt tail: stop at last commit
            if record.get("type") == "header":
                if record.get("version") != STATE_VERSION:
                    raise SystemExit(
                        "state file %s has version %r, expected %r"
                        % (path, record.get("version"), STATE_VERSION)
                    )
                header_seen = True
                base_rules = record.get("base_rules", {})
            elif record.get("type") == "event":
                events[str(record["event"]["id"])] = record["event"]
    return header_seen, base_rules, events


def load_state(path):
    state = new_state(path)
    if path is None:
        return state
    header_seen, base_rules, events = read_journal(path)
    state["header_seen"] = header_seen
    state["base_rules"] = base_rules
    state["events"] = events
    return state


def ensure_journal(state, path):
    """Create the journal with its header if it does not exist yet.

    The header pins the already-effective rules, so a resumed run reads the
    journaled events exactly as the run that committed them did.
    """
    if path is None or state["header_seen"]:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"type": "header", "version": STATE_VERSION, "base_rules": state["base_rules"]}
    if path.exists():
        # Only a torn header (no newline) can leave a file without one.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    else:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    state["header_seen"] = True


def append_commit(state, event, path):
    """Append one commit record; this is the transaction commit point."""
    state["events"][str(event["id"])] = event
    if path is None:
        return
    record = {"type": "event", "event": event, "digest": event_digest(event)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# ---------------------------------------------------------------------------
# derived (business) state

def new_derived():
    return {
        "capture": {},         # source<SEP>partition<SEP>seq -> capture record
        "approvals": {},       # str(approval event id) -> approval record
        "revokes": {},         # str(approval event id) -> revoking event id
        "ledger": {},          # business key -> current row or tombstone
        "quarantine": {},      # source<SEP>partition<SEP>seq -> quarantined row
        "conflicts": [],       # same seq, different content
        "received": {},        # "source/partition" -> sorted received seqs
        "backfilled": [],      # applied backfills, in decision order
        "audit": [],           # decisions, in decision order
    }


def apply_event(derived, base_rules, event):
    """Apply one accepted event to derived state."""
    kind = event.get("kind")
    if kind == "row":
        handle_row(derived, base_rules, event)
    elif kind == "approve":
        handle_approve(derived, event)
    elif kind == "revoke":
        handle_revoke(derived, event)
    else:
        derived["audit"].append({"id": event.get("id"), "action": "ignored_event"})


def derive(events, base_rules):
    """Replay accepted events in ascending event id order.

    Sorted replay makes the outcome independent of arrival order and of how a
    batch was chunked; the accepted events are the single source of truth.
    """
    state = new_derived()
    for event in events:
        apply_event(state, base_rules, event)
    return state


# ---------------------------------------------------------------------------
# cursor bookkeeping: cursor = largest seq contiguous from 1

def note_received(state, source, partition, seq):
    """Mark a source seq as durably received (also for quarantined rows)."""
    state["received"].setdefault(f"{source}/{partition}", set()).add(seq)


def compute_cursors(state):
    """Largest seq contiguous from 1 per partition: the cursor never spans holes."""
    cursors = {}
    for part in sorted(state["received"]):
        present = state["received"][part]
        value = 0
        while value + 1 in present:
            value += 1
        cursors[part] = value
    return cursors


# ---------------------------------------------------------------------------
# mapping selection and conversion

def valid_rule(rule):
    if not isinstance(rule, dict):
        return False
    if not isinstance(rule.get("amount_field"), str):
        return False
    if not isinstance(rule.get("enum_field"), str):
        return False
    if not isinstance(rule.get("enum"), dict):
        return False
    return is_int(rule.get("multiplier"))


def effective_mapping(state, base_rules, source, partition, schema, seq):
    """Approved mapping governing one capture row.

    The winner is the non-revoked approval for this exact (source, partition,
    schema) whose closed seq interval covers the row, preferring the largest
    approval event id.  Rows outside any approved interval fall back to the
    already-effective initial rule for (source, schema).  Otherwise there is
    no mapping and the row must be quarantined as an unknown version.
    """
    best = None
    for approval in state["approvals"].values():
        if approval.get("revoked"):
            continue
        if approval.get("source") != source:
            continue
        if approval.get("partition") != partition:
            continue
        if approval.get("schema") != schema:
            continue
        lower, upper = approval.get("from_seq"), approval.get("through_seq")
        if not lower <= seq <= upper:
            continue
        if best is None or approval["id"] > best["id"]:
            best = approval
    if best is not None:
        return best["rule"]
    return base_rules.get(mapping_key(source, schema))


def convert(rule, data):
    """Return (amount, status, reason, column) for one row payload.

    Amounts are multiplied in the integer domain; nulls, booleans and strings
    are never coerced to a number, and enum values must exist in the mapping.
    A missing amount column is reported against the amount field first, since
    the row cannot be valued at all without it.
    """
    amount_field = rule.get("amount_field")
    enum_field = rule.get("enum_field")
    if not isinstance(data, dict):
        return None, None, "invalid_amount", amount_field

    raw_amount = data[amount_field] if amount_field in data else MISSING
    if raw_amount is MISSING or not is_int(raw_amount):
        return None, None, "invalid_amount", amount_field

    raw_status = data[enum_field] if enum_field in data else MISSING
    enum = rule.get("enum")
    if not isinstance(raw_status, str) or not isinstance(enum, dict):
        return None, None, "invalid_enum", enum_field
    if raw_status not in enum:
        return None, None, "invalid_enum", enum_field

    return raw_amount * rule["multiplier"], enum[raw_status], None, None


# ---------------------------------------------------------------------------
# business ledger

def apply_change(state, source, partition, key, revision, op, amount, status):
    """Apply an upsert/delete only when the revision is strictly newer.

    Returns applied / deleted / stale_revision.  Equal or older revisions never
    change the visible value and never resurrect a tombstone.
    """
    bkey = business_key(source, partition, key)
    current = state["ledger"].get(bkey)
    if current is not None and revision <= current["revision"]:
        return "stale_revision"

    record = {
        "source": source,
        "partition": partition,
        "key": key,
        "revision": revision,
        "deleted": op == "delete",
    }
    if op != "delete":
        record["amount_cents"] = amount
        record["status"] = status
    state["ledger"][bkey] = record
    return "deleted" if op == "delete" else "applied"


def audit_decision(state, event, action, **extra):
    entry = {"id": event.get("id"), "action": action}
    if event.get("kind") == "row":
        entry["source"] = event.get("source")
        entry["partition"] = event.get("partition")
        entry["seq"] = event.get("seq")
        entry["schema"] = event.get("schema")
    entry.update(extra)
    state["audit"].append(entry)


# ---------------------------------------------------------------------------
# capture rows

def row_is_well_formed(event):
    if not isinstance(event.get("source"), str):
        return False
    if not isinstance(event.get("partition"), str):
        return False
    if not is_int(event.get("seq")) or event["seq"] < 1:
        return False
    if not isinstance(event.get("key"), str):
        return False
    if not is_int(event.get("revision")):
        return False
    return event.get("op") in ROW_OPS


def handle_row(state, base_rules, event):
    if not row_is_well_formed(event):
        # Not interpretable as a capture row: record the decision, but do not
        # acknowledge (and therefore do not advance) the sequence number.
        audit_decision(state, event, "malformed_row")
        return

    source, partition, seq = event["source"], event["partition"], event["seq"]
    schema = event.get("schema")
    ckey = capture_key(source, partition, seq)
    digest = event_digest(event)
    prior = state["capture"].get(ckey)

    if prior is not None:
        if prior["digest"] != digest:
            state["conflicts"].append({
                "source": source,
                "partition": partition,
                "seq": seq,
                "id": event.get("id"),
                "reason": "sequence_conflict",
            })
            audit_decision(state, event, "sequence_conflict")
        # Identical content under another event id is the same capture, and in
        # both cases the previously stored record is preserved untouched.
        return

    state["capture"][ckey] = {
        "event_id": event.get("id"),
        "digest": digest,
        "key": event["key"],
        "revision": event["revision"],
        "op": event["op"],
        "data": event.get("data"),
        "schema": schema,
    }
    # Persisting the capture acknowledges the seq, even when it is quarantined.
    note_received(state, source, partition, seq)

    rule = effective_mapping(state, base_rules, source, partition, schema, seq)
    if rule is None:
        state["quarantine"][ckey] = {
            "source": source,
            "partition": partition,
            "seq": seq,
            "schema": schema,
            "reason": "unknown_schema",
            "column": None,
            "data": event.get("data"),
        }
        audit_decision(state, event, "quarantined")
        return

    if event["op"] == "delete":
        outcome = apply_change(
            state, source, partition, event["key"],
            event["revision"], "delete", None, None,
        )
        audit_decision(state, event, "accepted" if outcome == "deleted" else "stale_revision")
        return

    amount, status, reason, column = convert(rule, event.get("data"))
    if reason is not None:
        state["quarantine"][ckey] = {
            "source": source,
            "partition": partition,
            "seq": seq,
            "schema": schema,
            "reason": reason,
            "column": column,
            "data": event.get("data"),
        }
        audit_decision(state, event, "quarantined")
        return

    outcome = apply_change(
        state, source, partition, event["key"],
        event["revision"], "upsert", amount, status,
    )
    audit_decision(state, event, "accepted" if outcome == "applied" else "stale_revision")


# ---------------------------------------------------------------------------
# approvals and revocations

def approval_is_well_formed(event):
    if not isinstance(event.get("source"), str):
        return False
    if not isinstance(event.get("partition"), str):
        return False
    if not isinstance(event.get("schema"), str):
        return False
    if not is_int(event.get("from_seq")) or not is_int(event.get("through_seq")):
        return False
    return event["from_seq"] <= event["through_seq"]


def handle_approve(state, event):
    rule = event.get("rule")
    supplied = event.get("rule_sha256")
    if (
        not approval_is_well_formed(event)
        or not valid_rule(rule)
        or not isinstance(supplied, str)
        or supplied not in rule_digests(rule)
    ):
        # Only this approval is rejected; the rest of the batch continues.
        state["audit"].append({"id": event.get("id"), "action": "approval_rejected"})
        return

    approval = {
        "id": event["id"],
        "source": event["source"],
        "partition": event["partition"],
        "schema": event["schema"],
        "rule": rule,
        "rule_sha256": supplied,
        "from_seq": event["from_seq"],
        "through_seq": event["through_seq"],
        "revoked": str(event["id"]) in state["revokes"],
    }
    state["approvals"][str(event["id"])] = approval
    state["audit"].append({"id": event["id"], "action": "approved"})
    if not approval["revoked"]:
        backfill(state, approval)


def backfill(state, approval):
    """Replay quarantined rows inside the approved interval, ascending by seq."""
    source, partition = approval["source"], approval["partition"]
    schema = approval["schema"]
    lower, upper = approval["from_seq"], approval["through_seq"]

    matched = [
        (entry["seq"], ckey, entry)
        for ckey, entry in state["quarantine"].items()
        if entry["source"] == source
        and entry["partition"] == partition
        and entry.get("schema") == schema
        and lower <= entry["seq"] <= upper
    ]
    matched.sort(key=lambda item: item[0])

    applied = []
    for _seq, ckey, entry in matched:
        record = state["capture"].get(ckey)
        if record is None:
            continue
        op = record.get("op")
        if op == "delete":
            amount = status = None
        else:
            amount, status, reason, _column = convert(approval["rule"], record.get("data"))
            if reason is not None:
                # Still not interpretable under the approved rule: it stays
                # quarantined and no backfill entry is recorded for it.
                continue
        outcome = apply_change(
            state, entry["source"], entry["partition"], record.get("key"),
            record.get("revision"), op, amount, status,
        )
        applied.append((ckey, record, outcome))

    for ckey, record, outcome in applied:
        entry = state["quarantine"].pop(ckey)
        state["backfilled"].append({
            "id": record.get("event_id"),
            "source": entry["source"],
            "partition": entry["partition"],
            "seq": entry["seq"],
            "approval_id": approval["id"],
            "outcome": outcome,
        })
        state["audit"].append({
            "id": record.get("event_id"),
            "action": "backfilled",
            "source": entry["source"],
            "partition": entry["partition"],
            "seq": entry["seq"],
            "schema": entry["schema"],
            "approval_id": approval["id"],
        })


def handle_revoke(state, event):
    approval_id = event.get("approval_id")
    state["revokes"][str(approval_id)] = event.get("id")
    approval = state["approvals"].get(str(approval_id))
    if approval is None:
        # The approval event has not been seen yet; the revocation is stored
        # and takes effect as soon as that approval is registered.
        state["audit"].append({"id": event.get("id"), "action": "revoked"})
        return
    if approval.get("revoked"):
        state["audit"].append({"id": event.get("id"), "action": "revoke_rejected"})
        return
    approval["revoked"] = True
    # Committed rows keep their values; only later rows and pending backfills
    # lose this approval's authorization.
    state["audit"].append({"id": event.get("id"), "action": "revoked"})


# ---------------------------------------------------------------------------
# report

def build_report(state):
    rows, tombstones = [], []
    for record in sorted(
        state["ledger"].values(),
        key=lambda item: (item["source"], item["partition"], str(item["key"])),
    ):
        common = {
            "source": record["source"],
            "partition": record["partition"],
            "key": record["key"],
            "revision": record["revision"],
        }
        if record["deleted"]:
            tombstones.append(common)
        else:
            rows.append(dict(common, amount_cents=record["amount_cents"], status=record["status"]))

    quarantine = [
        {
            "source": entry["source"],
            "partition": entry["partition"],
            "seq": entry["seq"],
            "schema": entry.get("schema"),
            "reason": entry["reason"],
            "column": entry.get("column"),
            "data": entry.get("data"),
        }
        for entry in sorted(
            state["quarantine"].values(),
            key=lambda item: (item["source"], item["partition"], item["seq"]),
        )
    ]
    conflicts = [
        {
            "source": entry["source"],
            "partition": entry["partition"],
            "seq": entry["seq"],
            "id": entry["id"],
            "reason": entry["reason"],
        }
        for entry in sorted(
            state["conflicts"],
            key=lambda item: (item["source"], item["partition"], item["seq"], item["id"]),
        )
    ]

    return {
        "rows": rows,
        "tombstones": tombstones,
        "quarantine": quarantine,
        "conflicts": conflicts,
        "cursor": compute_cursors(state),
        "backfilled": list(state["backfilled"]),
        "audit": list(state["audit"]),
    }


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# driver

def fail_point(fail_at, event_id):
    """Return the failure hook name for this event id, if any."""
    if not fail_at:
        return None
    prefix, _, number = fail_at.partition(":")
    if not number.strip().lstrip("-").isdigit():
        return None
    return prefix if int(number) == event_id else None


def accepted_in_order(state):
    """Accepted events in event id order (the order every result is built in)."""
    return [state["events"][marker] for marker in sorted(state["events"], key=int)]


def run(sim, state_path=None, output_path=None, fail_at=None):
    session = {
        "state_path": resolve_path(state_path),
        "output_path": resolve_path(output_path),
    }
    state = load_state(session["state_path"])

    # The already-effective rules are part of the checkpoint: a resumed run
    # must interpret the accepted events exactly as the original run did.
    incoming_rules = {}
    for spec in sim.get("initial_rules", []):
        source, schema, rule = spec.get("source"), spec.get("schema"), spec.get("rule")
        if source is None or schema is None or rule is None:
            continue
        incoming_rules[mapping_key(source, schema)] = rule
    if not state["base_rules"]:
        state["base_rules"] = incoming_rules
    base_rules = state["base_rules"]
    ensure_journal(state, session["state_path"])

    # Resume: rebuild the derived view from the committed journal.
    state["derived"] = derive(accepted_in_order(state), base_rules)
    highest_committed = max((int(marker) for marker in state["events"]), default=0)
    stale = False

    events = [
        event for event in sim.get("events", [])
        if is_int(event.get("id"))
    ]
    events.sort(key=lambda event: event["id"])

    for event in events:
        event_id = event["id"]
        marker = str(event_id)
        if marker in state["events"]:
            # A redelivered event keeps its first committed decision, which
            # also makes re-chunked or reordered replays idempotent.
            continue

        # Business update is applied in memory ahead of the commit.  Events
        # arriving below already-committed ids cannot be appended incrementally,
        # so the view is marked stale and rebuilt from the journal at the end.
        if not stale and event_id > highest_committed:
            apply_event(state["derived"], base_rules, event)   # staged update
        else:
            stale = True

        hook = fail_point(fail_at, event_id)
        if hook in ("after-row", "before-commit"):
            # The row is applied in memory and the transaction is prepared,
            # but nothing is written: the event stays pending and a resume
            # replays it against the previous committed checkpoint.
            os._exit(75)

        append_commit(state, event, session["state_path"])
        highest_committed = max(highest_committed, event_id)

        if hook == "before-ack":
            # Committed and durable, but the acknowledgement never returns.
            # The next run replays the input against this same journal.
            os._exit(75)

    if stale:
        # Replay the journal so the published view depends only on the
        # accepted set, not on the order in which it arrived.
        state["derived"] = derive(accepted_in_order(state), base_rules)
    return build_report(state["derived"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("simulation", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--fail-at")
    args = parser.parse_args()

    sim = json.loads(args.simulation.read_text(encoding="utf-8"))
    report = run(sim, args.state, args.output, args.fail_at)
    write_report(args.output, report)


if __name__ == "__main__":
    main()
