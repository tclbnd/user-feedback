#!/usr/bin/env python3
"""Weighted fair-share preemptive scheduler with segmented accounting.

Public API
----------
    simulate(sim[, chunk_size])   full scheduler result
    run(sim)                      alias of simulate (backwards compatible)
    replay(events, horizon, ...)  re-derive bills from an event stream
    simulate_chunked(sim, chunk)  same decisions, tick loop fed in chunks
    replay_chunked(events, ...)   same bills, event stream replayed in chunks

Design
------
A single CPU is shared *by weight*.  Each unit of service advances a job's
virtual time by ``1/w``; always choosing the smallest virtual time yields the
share ``w / sum(w)`` for every continuously backlogged job.  Rather than
quantising to 1-second quanta (which skews shares), the scheduler works in
*epochs*: between two "events" -- an arrival, a completion, or a virtual-time
catch-up -- the ready set is fixed, so the exact proportional split inside an
epoch can be computed in closed form.  This makes shares accurate even for
small horizons.

Preemption
----------
A heavier job that *arrives* may take the CPU from a lighter running job.
That is not a kill: the victim's served work is checkpointed (``served`` is
retained, ``remaining`` shrinks), and on resume it continues from the
checkpoint.  Every scheduling boundary becomes a ``start`` / ``pause`` /
``resume`` / ``finish`` event, so a bill equals the sum of disjoint run
segments and can never double-count wall-clock time.

Fairness guards
---------------
* Starvation bound: any ready job that has waited ``STARVATION_BOUND``
  seconds is force-run, bounding low-weight starvation under a heavy burst.
* Anti-thrash: once a job has been preempted more than ``PREEMPT_LIMIT``
  times, it earns an ``IMMUNITY_SECONDS`` window during which a new arrival
  cannot preempt it again.

Replay consistency
------------------
``replay`` consumes only the event stream, keeps at most one open segment per
job, ignores duplicate or late events for a finished job (so a rolled-back
preemption cannot emit a second completion), and produces identical bills no
matter how the stream is chunked.  Chunking the *simulator* tick loop or the
*arrival list* likewise leaves decisions unchanged.
"""

import argparse
import json
import sys

EPS = 1e-9
# Virtual-time comparisons use a looser tolerance than time comparisons:
# floating-point drift between backlogged jobs is normal, and a spuriously
# tiny catch-up gap would otherwise create an endless series of micro-epochs.
VTOL = 1e-6
MIN_STEP = 1e-6
# A remainder this small is treated as finished and snapped to the exact
# cost, so a job cannot hover just above zero and stall the fair-share clock.
REMAIN_TOL = 1e-6

PREEMPT_LIMIT = 3        # preempt_count > PREEMPT_LIMIT  => immune window
IMMUNITY_SECONDS = 20.0  # length of the preemption-immunity window
STARVATION_BOUND = 20.0  # max wait before a ready job is force-run

EVENT_KINDS = ("start", "pause", "resume", "finish")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(x):
    """JSON-friendly number: ints stay ints, floating noise is trimmed."""
    if x is None:
        return None
    f = float(x)
    if abs(f - round(f)) < 1e-9:
        return int(round(f))
    return round(f, 9)


def _job_key(job):
    return str(job.id)


def _extract_jobs(sim):
    """Accept a flat job list or the same stream split into chunks."""
    if "jobs" in sim:
        return list(sim["jobs"])
    chunks = sim.get("chunks")
    if chunks is not None:
        out = []
        for chunk in chunks:
            out.extend(chunk.get("jobs", []) if isinstance(chunk, dict)
                       else chunk)
        return out
    return list(sim.get("arrivals", []))


class Job:
    __slots__ = ("id", "w", "cost", "arrive", "rate", "served", "v",
                 "admitted", "finished", "started", "seg_start", "preempts",
                 "immune_until", "last_service_end")

    def __init__(self, d):
        self.id = d["id"]
        self.w = float(d.get("w", 1))
        if self.w <= 0:
            raise ValueError("weight must be positive for job %r" % (self.id,))
        self.cost = float(d.get("cost", 0))
        self.arrive = float(d.get("arrive", 0))
        self.rate = float(d.get("rate", 1))

        self.served = 0.0
        self.v = 0.0
        self.admitted = False
        self.finished = False
        self.started = False
        self.seg_start = None
        self.preempts = 0
        self.immune_until = float("-inf")
        self.last_service_end = self.arrive

    @property
    def remaining(self):
        return max(0.0, self.cost - self.served)


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------
class Scheduler:
    """Deterministic, replayable weighted fair-share scheduler."""

    def __init__(self, sim, chunk_size=None):
        self.jobs = [Job(d) for d in _extract_jobs(sim)]
        self.horizon = float(sim.get("horizon", 60))
        try:
            self.quantum = float(sim.get("quantum", 1) or 1)
        except (TypeError, ValueError):
            self.quantum = 1.0
        if self.quantum <= 0:
            self.quantum = 1.0
        self.chunk_size = chunk_size

        self.events = []
        self.finish_order = []
        self.decisions = []
        self.runner = None
        self.pending = sorted(self.jobs, key=lambda j: (j.arrive, _job_key(j)))
        self.pending_pos = 0
        self.arrivals_now = []

    # -- event stream ------------------------------------------------------
    def _emit(self, t, job, kind):
        """Append an event with a full-precision timestamp.

        Times are deliberately not rounded: a bill is a sum of segment
        differences, and rounding each boundary would let many small
        segments drift away from the true served time.  Rounding is applied
        only when serialising the final report.
        """
        if kind not in EVENT_KINDS:
            raise ValueError("unknown event kind %r" % (kind,))
        self.events.append({"t": float(t), "job": job.id, "kind": kind})

    def _open(self, t, job):
        """Open a run segment (``start`` first time, else ``resume``)."""
        if job.finished or job.seg_start is not None:
            return
        job.seg_start = t
        if job.started:
            self._emit(t, job, "resume")
        else:
            job.started = True
            self._emit(t, job, "start")

    def _close(self, t, job):
        """Close a run segment; served work stays in the checkpoint."""
        if job.seg_start is None:
            return
        self._emit(t, job, "pause")
        job.seg_start = None

    def _finish(self, t, job):
        if job.finished:
            return
        self._emit(t, job, "finish")
        job.finished = True
        job.seg_start = None
        self.finish_order.append(job.id)

    # -- admission / ready set ---------------------------------------------
    def _ready(self):
        return [j for j in self.jobs
                if j.admitted and not j.finished and j.remaining > EPS]

    def _admit(self, now):
        """Admit arrivals with ``arrive <= now`` and give them fresh credit."""
        self.arrivals_now = []
        ready = self._ready()
        base = min((j.v for j in ready), default=0.0)
        while (self.pending_pos < len(self.pending)
               and self.pending[self.pending_pos].arrive <= now + EPS):
            job = self.pending[self.pending_pos]
            self.pending_pos += 1
            job.admitted = True
            job.v = base
            job.last_service_end = max(now, job.arrive)
            if job.remaining <= EPS:
                self._finish(now, job)   # zero-work job: one record only
                continue
            self.arrivals_now.append(job)

    @staticmethod
    def _immune(job, now):
        return now < job.immune_until - EPS

    @staticmethod
    def _order_key(job):
        return (-job.w, job.arrive, _job_key(job))

    def _aged(self, now, ready):
        aged = [j for j in ready
                if now - j.last_service_end >= STARVATION_BOUND - EPS]
        aged.sort(key=lambda j: (j.last_service_end, j.arrive, _job_key(j)))
        return aged

    def _next_arrival(self):
        if self.pending_pos < len(self.pending):
            return self.pending[self.pending_pos].arrive
        return None

    # -- planning ----------------------------------------------------------
    def _plan(self, now):
        """Decide who runs at ``now``.

        Returns ``("exclusive", job, reason, victim)`` when one job must own
        the CPU (preemption or starvation rescue), else
        ``("share", None, None, None)`` so the fixed ready set is split by
        weight.  ``victim`` is the job that was preempted, if any.
        """
        ready = self._ready()
        if not ready:
            if self.runner is not None:
                self._close(now, self.runner)
                self.runner = None
            return None

        # 1) Starvation rescue: the longest-waiting overdue job runs alone.
        aged = self._aged(now, ready)
        if aged:
            return ("exclusive", aged[0], "starved", None)

        # 2) Arrival preemption: a heavier arrival displaces a lighter
        #    runner, unless the runner is inside its anti-thrash immunity.
        victim = self.runner
        if (victim is not None and victim in ready and self.arrivals_now
                and not self._immune(victim, now)):
            heaviest = max(self.arrivals_now,
                           key=lambda j: (j.w, -j.arrive, _job_key(j)))
            if heaviest.w > victim.w + EPS:
                victim.preempts += 1
                if victim.preempts > PREEMPT_LIMIT:
                    victim.immune_until = max(victim.immune_until,
                                              now + IMMUNITY_SECONDS)
                return ("exclusive", heaviest, "preempted", victim)

        return ("share", None, None, None)

    def _wait_limits(self, ready):
        """Earliest moment another ready job would cross the starvation bound."""
        if not ready:
            return self.horizon
        return min(job.last_service_end + STARVATION_BOUND for job in ready)

    def _next_arrival(self):
        if self.pending_pos < len(self.pending):
            return self.pending[self.pending_pos].arrive
        return None

    def _epoch_end(self, now, group, weight, exclusive):
        """End of the fixed-decision epoch.

        In ``exclusive`` mode a single job owns the CPU, but only for one
        quantum: preemption and starvation rescue are a *handoff*, after
        which weighted fair queueing resumes.  Making the handoff bounded
        keeps shares exact -- an exclusive run that lasted a whole epoch
        would hand the victim's fair share to the preemptor.

        In ``share`` mode the whole group is split proportionally, and a
        laggard with a virtual-time deficit within ``VTOL`` is treated as
        already in the group so floating-point drift cannot manufacture an
        endless sequence of near-zero catch-up epochs.
        """
        end = self.horizon
        nxt = self._next_arrival()
        if nxt is not None and nxt > now + EPS:
            end = min(end, nxt)
        if exclusive:
            end = min(end, now + self.quantum)
            end = min(end, now + group[0].remaining)
            return min(max(end, now + MIN_STEP), self.horizon)

        for job in group:
            end = min(end, now + job.remaining * weight / job.w)
        vmin = min(j.v for j in group)
        for job in self._ready():
            if job.v > vmin + VTOL:
                # The group clock advances at 1/weight per second, so a
                # laggard joins after (v - vmin) * weight seconds.
                end = min(end, now + (job.v - vmin) * weight)
        end = min(end, self._wait_limits(self._ready()))
        return min(max(end, now + MIN_STEP), self.horizon)

    # -- execution ---------------------------------------------------------
    def _switch(self, t, job):
        """Make ``job`` the runner, closing the previous segment if needed."""
        if self.runner is job and job.seg_start is not None:
            return
        if self.runner is not None:
            self._close(t, self.runner)
        self.runner = job
        self._open(t, job)

    def _advance(self, start, step, job):
        """Charge ``step`` seconds to ``job`` and close it if it is done.

        If only a sub-microsecond sliver of work remains, the sliver is
        accounted for in both the served counter and the event timeline, so
        the emitted bill is exactly the job's cost and the clock stays
        monotonic and horizon-bounded.
        """
        job.served += step
        job.v += step / job.w
        end = start + step
        sliver = job.remaining
        if sliver <= REMAIN_TOL:
            if sliver > 0:
                end = min(end + sliver, self.horizon)
                job.v += sliver / job.w
                job.served = job.cost
            job.last_service_end = end
            self._finish(end, job)
            if self.runner is job:
                self.runner = None
        else:
            job.last_service_end = end

    def _serve_epoch(self, now, group, weight, end):
        """Split ``[now, end)`` among ``group`` (one segment per member)."""
        total = end - now
        cursor = now
        for job in group:
            if cursor >= end - EPS or job.finished:
                continue
            piece = total * job.w / weight
            step = min(piece, job.remaining, end - cursor)
            if step <= EPS:
                continue
            self._switch(cursor, job)
            self._advance(cursor, step, job)
            cursor += step
        # Floating-point tail: keep the CPU busy without exceeding a member's
        # remaining work.
        if cursor < end - MIN_STEP:
            left = [j for j in group if not j.finished and j.remaining > EPS]
            if left:
                job = max(left, key=lambda j: (j.w, -j.arrive, _job_key(j)))
                step = min(end - cursor, job.remaining)
                self._switch(cursor, job)
                self._advance(cursor, step, job)

    def _step(self, now):
        """Advance from ``now`` to the next decision point; return that time."""
        self._admit(now)
        mode = self._plan(now)
        if mode is None:
            nxt = self._next_arrival()
            if nxt is None:
                return self.horizon
            return min(max(nxt, now + MIN_STEP), self.horizon)
        kind, pick, reason, victim = mode

        ready = self._ready()
        if kind == "exclusive":
            group = [pick]
            weight = pick.w
        else:
            vmin = min(j.v for j in ready)
            group = sorted((j for j in ready if j.v <= vmin + VTOL),
                           key=self._order_key)
            weight = sum(j.w for j in group)
        end = self._epoch_end(now, group, weight, kind == "exclusive")

        record = {
            "t": _num(now),
            "until": _num(end),
            "mode": kind,
            "reason": reason,
            "jobs": [j.id for j in group],
            "weights": {j.id: _num(j.w) for j in group},
            "share": {j.id: _num(j.w / weight) for j in group},
        }
        if victim is not None:
            record["victim"] = victim.id
            record["preempt_count"] = victim.preempts
            record["immune_until"] = (_num(victim.immune_until)
                                      if victim.immune_until > now
                                      else None)
        self.decisions.append(record)
        self._serve_epoch(now, group, weight, end)
        return end

    def run(self):
        """Drive the simulation, optionally in chunks.

        ``chunk_size`` only decides how often control returns to the caller:
        each call advances to the next natural decision point and no state is
        reset or rounded at a window edge, so every chunking produces exactly
        the same events and bills.  A step may overshoot a window; the next
        window simply starts from wherever the clock actually is.
        """
        limit = self.horizon
        chunk = self.chunk_size if self.chunk_size and self.chunk_size > 0 \
            else limit or 1.0
        now = 0.0
        window = 0.0
        guard = 0
        while window < limit - EPS and guard < 10000000:
            window_end = min(limit, window + chunk)
            while now < window_end - EPS and guard < 10000000:
                guard += 1
                reached = self._step(now)
                if reached <= now + MIN_STEP:
                    # Guarantee forward progress even if a pathological
                    # virtual-time tie would otherwise stall the clock.
                    reached = now + MIN_STEP
                now = min(reached, limit)
            window = window_end

        if self.runner is not None:
            self._close(limit, self.runner)
            self.runner = None
        return self._result()

    def _result(self):
        ids = [j.id for j in self.jobs]
        bills = replay(self.events, horizon=self.horizon, jobs=ids)["bills"]
        busy = sum(bills.values())
        util = busy / self.horizon if self.horizon > EPS else 0.0

        total_w = sum(j.w for j in self.jobs)
        details = {}
        for job in self.jobs:
            effective = bills.get(job.id, 0.0)
            details[job.id] = {
                "weight": _num(job.w),
                "cost": _num(job.cost),
                "arrive": _num(job.arrive),
                "served": _num(job.served),
                "remaining": _num(job.remaining),
                "rate": _num(job.rate),
                "effective_sec": _num(effective),
                "bill": _num(effective * job.rate),
                "checkpoint": _num(job.served),
                "preempts": job.preempts,
                "finished": job.finished,
                "fair_share": _num(job.w / total_w) if total_w else 0.0,
                "actual_share": _num(effective / busy) if busy > EPS else 0.0,
            }

        return {
            "events": list(self.events),
            "bills": {k: _num(v) for k, v in bills.items()},
            "preempts": {j.id: j.preempts for j in self.jobs},
            "finish_order": list(self.finish_order),
            "util": _num(util),
            "decisions": list(self.decisions),
            "share_util": {j.id: _num(bills.get(j.id, 0.0) / busy)
                           if busy > EPS else 0.0 for j in self.jobs},
            "details": details,
        }


# ---------------------------------------------------------------------------
# event-stream replay / billing
# ---------------------------------------------------------------------------
def replay(events, horizon=None, jobs=None, chunks=None, chunk_size=None,
           preempts=None):
    """Re-derive bills from start/pause/resume/finish events.

    At most one segment per job is open, so a bill is a sum of disjoint
    intervals.  A second ``finish`` for a job -- a rolled-back or duplicated
    completion -- is ignored, as is any event for an already-finished job.
    ``chunks`` / ``chunk_size`` only batch the stream; replay state is never
    reset at a chunk boundary, so every chunking yields the same bills.
    """
    event_list = list(events)
    job_ids = _normalise_job_ids(jobs)
    for ev in event_list:
        jid = ev.get("job")
        if jid is not None and jid not in job_ids:
            job_ids.append(jid)

    bills = {jid: 0.0 for jid in job_ids}
    open_at = {}
    finished = set()
    finish_order = []
    pause_events = {}

    def apply(ev):
        jid = ev.get("job")
        kind = ev.get("kind")
        if jid is None or kind not in EVENT_KINDS:
            return  # tolerate unknown record types
        t = float(ev.get("t", 0))
        if jid not in bills:
            bills[jid] = 0.0
        if kind in ("start", "resume"):
            if jid in finished or jid in open_at:
                return  # redundant segment open
            open_at[jid] = t
        elif kind == "pause":
            pause_events[jid] = pause_events.get(jid, 0) + 1
            if jid in open_at:
                bills[jid] += max(0.0, t - open_at.pop(jid))
        else:  # finish
            if jid in finished:
                return  # duplicate completion: neither bill nor record twice
            if jid in open_at:
                bills[jid] += max(0.0, t - open_at.pop(jid))
            finished.add(jid)
            finish_order.append(jid)

    if chunks is not None:
        for chunk in chunks:
            for ev in chunk:
                apply(ev)
    elif chunk_size is not None and chunk_size > 0:
        for i in range(0, len(event_list), chunk_size):
            for ev in event_list[i:i + chunk_size]:
                apply(ev)
    else:
        for ev in event_list:
            apply(ev)

    if horizon is None:
        horizon = max((float(ev.get("t", 0)) for ev in event_list), default=0.0)
    horizon = float(horizon)
    # A segment still open when the stream ends runs to the horizon.
    for jid, start in open_at.items():
        bills[jid] += max(0.0, horizon - start)

    busy = sum(bills.values())
    util = busy / horizon if horizon > EPS else 0.0
    preempts_out = ({} if preempts is None else
                    {jid: int(preempts.get(jid, 0)) for jid in job_ids})

    return {
        "events": event_list,
        "bills": {jid: _num(bills.get(jid, 0.0)) for jid in job_ids},
        "preempts": preempts_out,
        "finish_order": finish_order,
        "util": _num(util),
        "pause_events": pause_events,
    }


def _normalise_job_ids(jobs):
    if jobs is None:
        return []
    if isinstance(jobs, dict):
        jobs = jobs.get("jobs", jobs.get("arrivals", []))
    ids = []
    for j in jobs:
        jid = j.get("id") if isinstance(j, dict) else j
        if jid not in ids:
            ids.append(jid)
    return ids


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def simulate(sim, chunk_size=None):
    return Scheduler(sim, chunk_size=chunk_size).run()


def run(sim):
    return simulate(sim)


def simulate_chunked(sim, chunk_size=1):
    """Identical decisions when the tick loop is fed in chunks."""
    return simulate(sim, chunk_size=chunk_size)


def replay_chunked(events, horizon=None, jobs=None, chunk_size=1, chunks=None):
    return replay(events, horizon=horizon, jobs=jobs, chunk_size=chunk_size,
                  chunks=chunks)


def replay_bills(events, horizon=None, jobs=None, chunk_size=None):
    return replay(events, horizon=horizon, jobs=jobs,
                  chunk_size=chunk_size)["bills"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="weighted fair-share preemptive scheduler")
    ap.add_argument("sim", help="input simulation JSON")
    ap.add_argument("output", help="output JSON")
    ap.add_argument("--chunk", type=int, default=None,
                    help="process ticks/events in chunks (same decisions)")
    ap.add_argument("--replay", action="store_true",
                    help="treat the input as an event stream and re-bill it")
    args = ap.parse_args(argv)

    with open(args.sim, "r", encoding="utf-8") as f:
        data = json.load(f)

    is_replay = args.replay or ("events" in data and "jobs" not in data)
    if is_replay:
        result = replay(data.get("events", []),
                        horizon=data.get("horizon"),
                        jobs=data.get("jobs"),
                        chunks=data.get("chunks"),
                        chunk_size=args.chunk,
                        preempts=data.get("preempts"))
        result.pop("pause_events", None)
        result.pop("events", None)
    else:
        result = simulate(data, chunk_size=args.chunk)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

