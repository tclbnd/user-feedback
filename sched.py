#!/usr/bin/env python3
"""Multi-source page-group scheduler.

Hierarchical (global + per-source) integer budgets, deficit-weighted round
robin across tenants, stable (source, task, part) de-duplication and
atomically checkpointed crash recovery. The entry point stays
``python3 sched.py sim.json output.json`` and every scenario is replayed from
JSON events ordered by ``(tick, seq)``.
"""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

EXIT_INJECTED = 75


def _atomic_write_json(path, payload):
    """Fsync to a temp file then rename-swap: a page group's completion, both
    budget debits and its de-duplication record can only appear together."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False,
                      separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class Scheduler:
    """Deterministic replay engine; all mutable progress lives in state."""

    def __init__(self, sim, state_path=None, fail_at=None):
        self.sim = sim
        self.state_path = Path(state_path) if state_path is not None else None
        self.fail_at = fail_at
        self.horizon = int(sim["horizon"])
        self.weights = dict(sim["weights"])
        self.tenants = sorted(self.weights)
        # Raw input order must never change the result: replay by (tick, seq).
        self.events = sorted(
            (dict(event) for event in sim["events"]),
            key=lambda event: (event["tick"], event["seq"]),
        )

        if self.state_path is not None and self.state_path.is_file():
            self.state = json.loads(
                self.state_path.read_text(encoding="utf-8"))
            self.groups = {tuple(g["key"]): g for g in self.state["groups"]}
            self.queues = {
                tenant: [tuple(key) for key in keys]
                for tenant, keys in self.state["queues"].items()
            }
            # Re-link open segments to the exact dict objects held in the
            # segments list (matched by unique id). JSON round-tripping would
            # otherwise leave two independent copies, so later service updates
            # never reach the reported backlog_segments.
            by_id = {seg["id"]: seg for seg in self.state["segments"]}
            open_seg = self.state["open_segment"]
            for tenant, seg in open_seg.items():
                if seg is not None and seg["id"] in by_id:
                    open_seg[tenant] = by_id[seg["id"]]
            if self.fail_at == "after-restore":
                os._exit(EXIT_INJECTED)
        else:
            self.groups = {}
            self.queues = {tenant: [] for tenant in self.tenants}
            self._fresh_state(sim)

    # ------------------------------------------------------------------ state
    def _fresh_state(self, sim):
        sources = list(sim["source_budget"])
        self.state = {
            "version": 1,
            "tick": 0,                 # next tick to initialize
            "initialized": False,      # event batch / budget install done
            "watermark": None,         # last consumed event as [tick, seq]
            "event_cursor": 0,         # index into the sorted event list
            "global_budget": int(sim["global_budget"]),
            "global_remaining": 0,
            "source_budget": {s: int(v)
                              for s, v in sim["source_budget"].items()},
            "source_remaining": {s: 0 for s in sources},
            "paused": {s: False for s in sources},
            "deficit": {tenant: 0 for tenant in self.tenants},
            "cursor": self.tenants[0] if self.tenants else None,
            "carry": False,            # resume current visit without weight
            "active_visit": None,      # tenant whose visit is in progress
            "visits_left": len(self.tenants),  # visits remaining this tick
            "groups": [],
            "queues": {tenant: [] for tenant in self.tenants},
            "dispatch": [],
            "committed": [],           # de-dup keys in commit order
            "service": {tenant: 0 for tenant in self.tenants},
            "paused_ticks": {s: 0 for s in sources},
            "balances": [],
            "waits": [],
            "segments": [],            # continuous backlog segments
            "next_segment_id": 0,
            "open_segment": {tenant: None for tenant in self.tenants},
            "dispatch_count": 0,
        }

    def checkpoint(self):
        if self.state_path is None:
            return
        # Flat JSON representation; indexed views are rebuilt on restore.
        self.state["groups"] = list(self.groups.values())
        self.state["queues"] = {
            tenant: [list(key) for key in keys]
            for tenant, keys in self.queues.items()
        }
        _atomic_write_json(self.state_path, self.state)

    # ------------------------------------------------------------------ events
    def _order(self, key):
        group = self.groups[key]
        # Spec dispatch order is (arrival tick, task, part); source is added
        # only as a final deterministic tie-breaker for distinct page groups
        # that share those three fields.
        return (group["arrival_tick"], group["task"], group["part"],
                group["source"])

    def _enqueue(self, tenant, key):
        """Insert keeping (arrival tick, task, part) order (bisect gained a
        key= argument only in Python 3.10, so do it manually)."""
        queue = self.queues[tenant]
        order = self._order(key)
        lo, hi = 0, len(queue)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._order(queue[mid]) < order:
                lo = mid + 1
            else:
                hi = mid
        queue.insert(lo, key)

    def _apply_events(self, tick):
        st = self.state
        events = self.events
        while (st["event_cursor"] < len(events)
               and events[st["event_cursor"]]["tick"] == tick):
            event = events[st["event_cursor"]]
            # Replayed after a crash mid-tick: already consumed events are
            # skipped by the watermark, so no duplicate arrival or debit can
            # ever be created.
            if st["watermark"] is not None and \
                    (event["tick"], event["seq"]) <= tuple(st["watermark"]):
                st["event_cursor"] += 1
                continue
            kind = event["kind"]
            if kind == "arrival":
                key = (event["source"], event["task"], event["part"])
                if key not in self.groups:
                    tenant = event["tenant"]
                    if tenant not in self.queues:
                        raise ValueError("unknown tenant %r" % tenant)
                    group = {
                        "key": list(key),
                        "source": event["source"],
                        "tenant": tenant,
                        "task": event["task"],
                        "part": event["part"],
                        "cost": int(event["cost"]),
                        "arrival_tick": tick,
                    }
                    self.groups[key] = group
                    self._segment_open(tenant, tick)
                    self._enqueue(tenant, key)
            elif kind == "pause":
                st["paused"][event["source"]] = bool(event["paused"])
            elif kind == "source_budget":
                st["source_budget"][event["source"]] = int(event["budget"])
                st["source_remaining"].setdefault(event["source"], 0)
                st["paused"].setdefault(event["source"], False)
                st["paused_ticks"].setdefault(event["source"], 0)
            elif kind == "global_budget":
                st["global_budget"] = int(event["budget"])
            else:
                raise ValueError("unknown event kind %r" % kind)
            st["watermark"] = [event["tick"], event["seq"]]
            st["event_cursor"] += 1

    # ------------------------------------------------------------- backlog segs
    def _segment_open(self, tenant, tick):
        open_seg = self.state["open_segment"]
        seg = open_seg[tenant]
        # Reuse the open segment only while it is genuinely still live at this
        # tick; a segment closed by an earlier (possibly replayed) drain must
        # never be re-opened and overwritten.
        if seg is not None and seg["end"] >= tick:
            return
        if seg is not None:
            # Stale reference to an already-closed segment: detach first.
            open_seg[tenant] = None
        new_seg = {"id": self.state["next_segment_id"], "tenant": tenant,
                   "start": tick, "end": tick, "service": 0}
        self.state["next_segment_id"] += 1
        open_seg[tenant] = new_seg
        self.state["segments"].append(new_seg)

    def _segment_service(self, tenant, cost):
        seg = self.state["open_segment"][tenant]
        seg["service"] += cost
        seg["end"] = self.state["tick"]

    def _segment_drain(self, tenant, tick):
        open_seg = self.state["open_segment"]
        if open_seg[tenant] is not None and not self.queues[tenant]:
            open_seg[tenant]["end"] = tick
            open_seg[tenant] = None

    def _close_tail_segments(self, last_tick):
        # Segments still open at the horizon end close at the final tick.
        for tenant, seg in self.state["open_segment"].items():
            if seg is not None:
                seg["end"] = max(last_tick, seg["start"])
                self.state["open_segment"][tenant] = None

    # ------------------------------------------------------------------ dispatch
    def _eligible(self, key):
        st = self.state
        group = self.groups[key]
        if st["paused"].get(group["source"], False):
            return False, "paused"
        cost = group["cost"]
        if (cost > st["global_remaining"]
                or cost > st["source_remaining"].get(group["source"], 0)):
            return False, "budget"
        if cost > st["deficit"][group["tenant"]]:
            return False, "rotation"
        return True, None

    def _visit(self, tenant):
        """One rotation visit.

        Returns a status string:
          * ``done``     - visit completed (drained or nothing dispatchable
                           with the global budget still alive); rotate on.
          * ``exhausted``- a dispatch drained the global budget; the tick ends
                           immediately and, per spec, this visit carries over.
        The second return value is how many groups this visit dispatched.
        """
        st = self.state
        queue = self.queues[tenant]
        dispatched = 0
        # Is this a re-entry into a visit that already ran (and checkpointed
        # part of its work) before a crash? Then its weight is already inside
        # the persisted deficit and must not be credited again.
        resumed = st["active_visit"] == tenant
        st["active_visit"] = tenant
        # Advance the rotation cursor as the first mutation of this visit so
        # every checkpoint written below (including the first debit) already
        # points at the tenant to visit next. A crash can therefore never
        # resume on a stale cursor and re-serve the same tenant.
        idx = self.tenants.index(tenant)
        st["cursor"] = self.tenants[(idx + 1) % len(self.tenants)]
        if not queue:
            # Empty queue: the visit ends immediately. This also reconciles a
            # resumed visit whose final debit (and group removal) was already
            # committed before a crash: its backlog segment must be closed at
            # the current tick and the deficit cleared, idempotently.
            st["deficit"][tenant] = 0
            st["carry"] = False
            st["active_visit"] = None
            self._segment_drain(tenant, st["tick"])
            self.checkpoint()
            # If the global budget is already gone, no later visit in this
            # resumed tick can dispatch either; end the tick right away.
            if st["global_remaining"] <= 0:
                return "exhausted", dispatched
            return "done", dispatched
        if not st["carry"] and not resumed:
            # A fresh visit to a non-empty queue earns one weight allowance.
            st["deficit"][tenant] += self.weights[tenant]
        # Consume the carry marker: it only describes how this visit started.
        st["carry"] = False
        if st["global_remaining"] <= 0:
            # Re-entering a tick whose global budget was already fully spent
            # before a crash: nothing can dispatch, so end the tick now. The
            # in-progress visit carries over (its deficit is already correct).
            st["carry"] = bool(queue) and st["deficit"][tenant] > 0
            st["active_visit"] = tenant if st["carry"] else None
            return "exhausted", dispatched
        while queue:
            chosen = None
            for key in queue:  # (arrival tick, task, part) order
                ok, _reason = self._eligible(key)
                if ok:
                    chosen = key
                    break
            if chosen is None:
                # Backlog remains but nothing is dispatchable in this visit.
                # The visit ends here: rotation moves to the next tenant. The
                # retained deficit is NOT topped up again this tick; the tenant
                # earns its weight again on its next fresh visit (next pass or
                # next tick). Only a fully exhausted global budget carries this
                # same visit across the tick boundary without a new weight.
                st["carry"] = st["global_remaining"] <= 0
                st["active_visit"] = tenant if st["carry"] else None
                return "done", dispatched
            group = self.groups[chosen]
            cost = group["cost"]
            ordinal = st["dispatch_count"] + 1
            if self.fail_at == "before-debit:%d" % ordinal:
                # Nothing has changed yet: the checkpoint still describes this
                # exact pending group and the same rotation position.
                os._exit(EXIT_INJECTED)

            # Atomic unit: both budget debits, deficit debit, de-dup record,
            # service share and committed sequence land in one checkpoint
            # document via a single rename-swap.
            st["global_remaining"] -= cost
            st["source_remaining"][group["source"]] -= cost
            st["deficit"][tenant] -= cost
            st["service"][tenant] += cost
            st["dispatch"].append({
                "tick": st["tick"],
                "source": group["source"],
                "tenant": tenant,
                "task": group["task"],
                "part": group["part"],
                "cost": cost,
            })
            st["committed"].append(list(chosen))
            st["dispatch_count"] += 1
            dispatched += 1
            self.groups.pop(chosen)
            queue.remove(chosen)
            self._segment_service(tenant, cost)
            self.checkpoint()
            if self.fail_at == "after-commit:%d" % ordinal:
                os._exit(EXIT_INJECTED)
            if not queue:
                # Backlog drained: reset the deficit and finish the visit.
                st["deficit"][tenant] = 0
                st["active_visit"] = None
                self._segment_drain(tenant, st["tick"])
                # Persist the drain (closed segment, cleared deficit) so a
                # crash after a later debit cannot leave this segment open.
                self.checkpoint()
            if st["global_remaining"] <= 0:
                # Global budget exhausted: end the tick immediately. Carry the
                # visit into the next tick on the same tenant (no new weight)
                # only while backlog and unspent deficit both remain.
                st["carry"] = bool(queue) and st["deficit"][tenant] > 0
                # Keep active_visit when carrying (resume same tenant, no new
                # weight); otherwise the visit is fully complete.
                if not st["carry"]:
                    st["active_visit"] = None
                return "exhausted", dispatched
        st["active_visit"] = None
        return "done", dispatched

    def _sweep_waits(self, tick):
        """Classify every still-pending group at tick end. Pause takes
        precedence, then missing two-level budget; otherwise the group is only
        waiting for its deficit rotation turn."""
        st = self.state
        for tenant in self.tenants:
            for key in self.queues[tenant]:
                group = self.groups[key]
                if st["paused"].get(group["source"], False):
                    reason = "paused"
                elif (group["cost"] > st["global_remaining"]
                      or group["cost"] > st["source_remaining"].get(
                          group["source"], 0)):
                    reason = "budget"
                else:
                    reason = "rotation"
                st["waits"].append({
                    "tick": tick,
                    "source": group["source"],
                    "tenant": tenant,
                    "task": group["task"],
                    "part": group["part"],
                    "reason": reason,
                })

    def _finish_tick(self):
        st = self.state
        tick = st["tick"]
        self._sweep_waits(tick)
        st["balances"].append({
            "tick": tick,
            "global": st["global_remaining"],
            "source": dict(st["source_remaining"]),
        })
        st["tick"] += 1
        st["initialized"] = False
        st["visits_left"] = len(self.tenants)
        # active_visit is intentionally retained across the boundary when a
        # visit carries; a normal tick end has already cleared it.
        self.checkpoint()

    def run(self):
        st = self.state
        while st["tick"] < self.horizon:
            tick = st["tick"]
            if not st["initialized"]:
                # Apply the tick's events in (tick, seq) order, then install
                # the fresh integer budgets; balances never roll over.
                self._apply_events(tick)
                st["global_remaining"] = st["global_budget"]
                st["source_remaining"] = dict(st["source_budget"])
                for source, frozen in st["paused"].items():
                    st["paused_ticks"][source] += int(bool(frozen))
                st["initialized"] = True
                self.checkpoint()

            # One rotating pass over the tenants per tick: each non-empty
            # queue is visited exactly once and earns its weight a single
            # time. A tick ends early when the global budget is exhausted or a
            # carried visit stops it; otherwise it ends after every tenant has
            # been visited. A full pass in which nobody can dispatch simply
            # falls through to the next tick.
            carry_tenant = None
            # Driven by a persisted counter so a mid-tick crash resumes the
            # partially completed pass instead of restarting it (which would
            # re-add weights and double-visit tenants). The counter is only
            # decremented AFTER a visit fully completes and checkpoints, so a
            # crash inside a visit re-runs that same visit, never skips one.
            while st["visits_left"] > 0:
                # A persisted active_visit means the previous process crashed
                # inside this exact visit: re-enter that tenant (whose weight
                # and debits already live in the checkpoint) rather than the
                # already-advanced rotation cursor.
                tenant = st["active_visit"] or st["cursor"]
                # _visit advances the rotation cursor itself and checkpoints
                # it together with every debit, so a crash always resumes at
                # the correct next tenant rather than a stale position.
                status, _dispatched = self._visit(tenant)
                # Commit the completed visit's consumption of a pass slot.
                st["visits_left"] -= 1
                self.checkpoint()
                # Only an explicitly carried visit (backlog + unspent deficit
                # at global exhaustion) resumes this same tenant next tick.
                # A spent-deficit visit still rotates to the next tenant.
                if st["carry"]:
                    carry_tenant = tenant
                    break
                if status == "exhausted":
                    # Global budget gone and the visit completed normally:
                    # nothing else can dispatch this tick.
                    break
            if carry_tenant is not None:
                # Resume this same in-progress visit next tick, no new weight.
                st["cursor"] = carry_tenant
                self.checkpoint()
            self._finish_tick()
            # The carry marker is consumed at the start of the next visit.
        self._close_tail_segments(self.horizon - 1)
        self.checkpoint()
        return self.report()

    def report(self):
        st = self.state
        report = {
            "dispatch": st["dispatch"],
            "balances": st["balances"],
            "service": {tenant: st["service"][tenant]
                        for tenant in self.tenants},
            "paused_ticks": dict(st["paused_ticks"]),
            "backlog_segments": [
                {
                    "tenant": seg["tenant"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "service": seg["service"],
                }
                for seg in st["segments"]
            ],
            "waits": st["waits"],
        }
        canonical = json.dumps(report, sort_keys=True,
                               separators=(",", ":"), ensure_ascii=False)
        report["digest"] = hashlib.sha256(
            canonical.encode("utf-8")).hexdigest()
        return report


def replay(sim, state=None, fail_at=None):
    scheduler = Scheduler(sim, state_path=state, fail_at=fail_at)
    return scheduler.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("simulation", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--fail-at")
    args = parser.parse_args()
    sim = json.loads(args.simulation.read_text(encoding="utf-8"))
    report = replay(sim, state=args.state, fail_at=args.fail_at)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
