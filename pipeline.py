#!/usr/bin/env python3
"""Multi-stage pipeline simulation with cascading backpressure.

One call to :func:`run` executes a deterministic sequence of logical ticks.
Every stage owns one bounded input buffer.  A producer never removes an item
from its buffer unless the downstream buffer has accepted it, so a full buffer
blocks its producer ("backpressure") instead of dropping data.

Backpressure protocol
---------------------
Each buffer exposes a waterline signal with hysteresis:

* ``low <= len < high`` -- producers are admitted normally;
* ``len >= high``       -- pressure asserted, producers stop.  If ``high < cap``
  the head-room up to ``cap`` is reserved for priority traffic;
* ``len <= low``        -- pressure released, producers resume.

The signal is latched, so a buffer hovering around a single watermark cannot
throttle and unthrottle its producer on alternate ticks.

Recovery
--------
A stall is an explicit downstream pause.  While it lasts the paused stage
consumes nothing, its inbound buffer fills and asserts pressure, its producer
blocks, that producer's inbound buffer then fills, and so on upstream
(cascading backpressure).  When the stall ends the last stage resumes first;
each upstream stage unblocks one tick later as the freed slot reaches it, so
recovery walks downstream -> upstream in order.  Every buffer is FIFO and an
item only leaves a buffer after being accepted downstream, therefore the
completion stream is strictly ordered: items of one generation can never be
interleaved with items of another generation.

Deadlock detection
------------------
A tick in which nothing moved while work is still pending is a candidate
wedge.  Instead of guessing from the absence of movement alone, the simulator
runs a side-effect-free probe: could any stage hand its head item downstream,
and could the source inject, if the declared pauses were lifted?  A stage
inside a declared stall counts as recoverable progress, so an injected pause --
a deliberate fault with a known end -- can never raise a false positive, while
a genuinely unusable topology (for instance a zero-capacity buffer) is still
reported as ``deadlock: true``, even inside a stall window.
"""

import json
import sys
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

MAX_TICKS_DEFAULT = 10000

BLOCK = "block"
DROP_NEWEST = "drop_newest"
DROP_OLDEST = "drop_oldest"
PRIORITY_PREEMPT = "priority_preempt"

ACCEPTED = "accepted"
DROPPED = "dropped"
BLOCKED = "blocked"

_POLICY_ALIASES = {
    "block": BLOCK,
    "wait": BLOCK,
    "retry": BLOCK,
    "backpressure": BLOCK,
    "drop_newest": DROP_NEWEST,
    "drop_new": DROP_NEWEST,
    "newest": DROP_NEWEST,
    "reject_new": DROP_NEWEST,
    "drop_oldest": DROP_OLDEST,
    "drop_old": DROP_OLDEST,
    "oldest": DROP_OLDEST,
    "reject_old": DROP_OLDEST,
    "evict_oldest": DROP_OLDEST,
    "priority_preempt": PRIORITY_PREEMPT,
    "preempt": PRIORITY_PREEMPT,
    "priority": PRIORITY_PREEMPT,
}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_policy(value: Any, default: str = BLOCK) -> str:
    """Map user supplied policy names onto the four supported policies."""
    if value is None:
        return default
    key = str(value).strip().lower().replace("-", "_")
    return _POLICY_ALIASES.get(key, default)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    name: str
    cap: int
    high: int
    low: int
    policy: str = BLOCK
    priority_threshold: Optional[int] = None


@dataclass(frozen=True)
class Stall:
    idx: int
    start: int
    end: int

    def active(self, tick: int) -> bool:
        return self.start <= tick < self.end


def _make_stage(raw: Dict[str, Any], index: int, default_policy: str,
                used_names: List[str]) -> Stage:
    name = str(raw.get("name", "s%d" % index))
    # Water levels and blocked ticks are keyed by stage name in the output, so
    # duplicates would silently merge two queues.  Disambiguate instead.
    if name in used_names:
        suffix = 2
        while "%s#%d" % (name, suffix) in used_names:
            suffix += 1
        name = "%s#%d" % (name, suffix)
    used_names.append(name)
    cap = max(0, _int(raw.get("cap", 4), 4))
    # The soft watermark defaults to the hard capacity; lowering it keeps
    # head-room that is only handed to priority traffic.
    high = _int(raw.get("high", raw.get("high_watermark", cap)), cap)
    low = _int(raw.get("low", raw.get("low_watermark", max(0, high - 1))),
               max(0, high - 1))
    high = max(0, min(high, cap))
    low = max(0, min(low, high))
    threshold = raw.get("priority_threshold")
    if threshold is not None:
        threshold = _int(threshold, 0)
    policy_raw = raw.get("reject_policy", raw.get("reject"))
    return Stage(
        name=name,
        cap=cap,
        high=high,
        low=low,
        policy=normalize_policy(policy_raw, default_policy),
        priority_threshold=threshold,
    )


def _normalize_stages(scenario: Dict[str, Any]) -> List[Stage]:
    raw_stages = scenario.get("stages") or []
    default_policy = normalize_policy(
        scenario.get("reject_policy", scenario.get("reject"))
    )
    used: List[str] = []
    return [
        _make_stage(raw, i, default_policy, used)
        for i, raw in enumerate(raw_stages)
    ]


def _normalize_stalls(scenario: Dict[str, Any], stages: Sequence[Stage]) -> List[Stall]:
    raw = scenario.get("stalls", scenario.get("stall"))
    if raw is None:
        return []
    entries = raw if isinstance(raw, list) else [raw]
    index_of = {stage.name: i for i, stage in enumerate(stages)}
    stalls: List[Stall] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("stage")
        idx: Optional[int] = None
        if isinstance(key, str):
            if key in index_of:
                idx = index_of[key]
            else:
                idx = _int(key, -1)
        elif key is not None:
            idx = _int(key, -1)
        if idx is None or not (0 <= idx < len(stages)):
            # A stall that names an unknown stage must not freeze an
            # unrelated queue; ignore it instead.
            continue
        start = max(0, _int(entry.get("from", entry.get("start", 0)), 0))
        end = entry.get("to", entry.get("end"))
        if end is None and "duration" in entry:
            end = start + max(0, _int(entry.get("duration"), 0))
        end = start if end is None else _int(end, start)
        if end > start:
            stalls.append(Stall(idx=idx, start=start, end=end))
    stalls.sort(key=lambda s: (s.start, s.idx))
    return stalls


def _is_stalled(stalls: Sequence[Stall], idx: int, tick: int) -> bool:
    return any(s.idx == idx and s.active(tick) for s in stalls)


def _progress_possible(stages: Sequence[Stage], buffers: Sequence["BoundedBuffer"],
                       pending_source: Optional["Item"],
                       stalls: Sequence[Stall], tick: int) -> bool:
    """Whether the pipeline could still move, now or once a pause ends.

    This is the deadlock probe.  It is a pure predicate: it never mutates any
    buffer.  A stage that is inside a declared stall counts as capable of
    progress -- the pause has a known end, so waiting on it is not a wedge.
    A pipeline that is quiet *only* because of an injected stall therefore
    never reports a deadlock, while one that could not move even after the
    pause lifted (a permanently unusable buffer, for instance) still does.
    """
    for i in range(len(stages)):
        buf = buffers[i]
        if not len(buf):
            continue
        if _is_stalled(stalls, i, tick):
            # Its input is waiting for a bounded, recoverable pause.
            return True
        if i + 1 == len(stages):
            return True                      # the sink can always consume
        if buffers[i + 1].would_admit(buf.peek()):
            return True
    if pending_source is not None and buffers:
        if buffers[0].would_admit(pending_source):
            return True
    return False


# ---------------------------------------------------------------------------
# Items and bounded buffers
# ---------------------------------------------------------------------------

@dataclass
class Item:
    seq: int
    value: Any
    priority: int = 0
    generation: int = 0


class BoundedBuffer:
    """Bounded FIFO input buffer implementing the waterline protocol.

    ``offer`` returns ``accepted``, ``dropped`` or ``blocked`` and never lets
    the buffer grow past ``cap``.  With the ``block`` policy a ``blocked``
    result is the backpressure signal: the caller keeps the item at the head
    of its own buffer (the retry channel) and tries again on a later tick.
    """

    __slots__ = ("stage", "items", "pressure", "accepted", "rejected",
                 "evicted", "blocked_attempts", "peak", "pressure_ticks")

    def __init__(self, stage: Stage) -> None:
        self.stage = stage
        self.items: Deque[Item] = deque()
        self.pressure = False
        self.accepted = 0
        self.rejected = 0        # refused by the configured rejection policy
        self.evicted = 0         # displaced by drop_oldest / priority_preempt
        self.blocked_attempts = 0
        self.peak = 0
        self.pressure_ticks = 0

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    # -- waterline ---------------------------------------------------------
    def _refresh_pressure(self) -> None:
        level = len(self.items)
        if level >= self.stage.high:
            self.pressure = True
        elif level <= self.stage.low:
            self.pressure = False

    def _priority_admitted(self, item: Item) -> bool:
        threshold = self.stage.priority_threshold
        return threshold is not None and item.priority >= threshold

    def would_admit(self, item: Item) -> bool:
        """Side-effect-free prediction of ``offer``.

        ``True`` means ``offer`` would consume the item (either accepting it,
        evicting an older one, or rejecting it by policy), so the producer
        could make progress.  ``False`` means ``offer`` would return
        ``BLOCKED`` and the producer would have to wait.
        """
        stage = self.stage
        if len(self.items) < stage.cap:
            if not self.pressure or self._priority_admitted(item):
                return True
        # Buffer is at its hard bound (or in reserved head-room).
        policy = stage.policy
        if policy == BLOCK:
            return False
        if policy == DROP_NEWEST:
            return True          # the newcomer is rejected: producer progresses
        if policy == DROP_OLDEST:
            return bool(self.items)   # always evicts the head when non-empty
        # priority_preempt always consumes the newcomer: either it displaces
        # a weaker item or it is rejected.  Only an empty buffer blocks.
        return True

    # -- admission ---------------------------------------------------------
    def offer(self, item: Item) -> str:
        stage = self.stage
        if len(self.items) >= stage.cap:
            return self._reject(item)
        if self.pressure and not self._priority_admitted(item):
            # Reserved head-room: the buffer is above its high watermark and
            # only priority traffic may use the remaining slots.
            return self._reject(item)
        self._admit(item)
        return ACCEPTED

    def _admit(self, item: Item) -> None:
        self.items.append(item)
        self.accepted += 1
        if len(self.items) > self.peak:
            self.peak = len(self.items)
        self._refresh_pressure()

    def _reject(self, item: Item) -> str:
        stage = self.stage
        policy = stage.policy
        if policy == BLOCK:
            self.blocked_attempts += 1
            return BLOCKED
        if policy == DROP_NEWEST:
            self.rejected += 1
            return DROPPED
        if policy == DROP_OLDEST:
            if not self.items:
                # Nothing to evict: fall back to blocking so the item stays
                # in the producer's retry channel instead of vanishing.
                self.blocked_attempts += 1
                return BLOCKED
            # Evicting before appending keeps the hard bound intact, so the
            # buffer is never momentarily over capacity.
            self.items.popleft()
            self.evicted += 1
            self.items.append(item)
            self.accepted += 1
            self._refresh_pressure()
            return ACCEPTED
        # priority_preempt
        victim = self._weakest_index()
        if victim is not None and item.priority > self.items[victim].priority:
            del self.items[victim]
            self.evicted += 1
            self.items.append(item)
            self.accepted += 1
            self._refresh_pressure()
            return ACCEPTED
        self.rejected += 1
        return DROPPED

    def _weakest_index(self) -> Optional[int]:
        """Index of the least important item (ties: the newest one)."""
        if not self.items:
            return None
        best = 0
        best_key = (self.items[0].priority, -self.items[0].seq)
        for i in range(1, len(self.items)):
            key = (self.items[i].priority, -self.items[i].seq)
            if key < best_key:
                best, best_key = i, key
        return best

    # -- consumption -------------------------------------------------------
    def peek(self) -> Item:
        return self.items[0]

    def pop(self) -> Item:
        item = self.items.popleft()
        self._refresh_pressure()
        return item

    def snapshot(self) -> Dict[str, Any]:
        return {
            "cap": self.stage.cap,
            "high": self.stage.high,
            "low": self.stage.low,
            "policy": self.stage.policy,
            "level": len(self.items),
            "peak": self.peak,
            "pressure": self.pressure,
            "accepted": self.accepted,
            "dropped": self.rejected + self.evicted,
            "rejected": self.rejected,
            "evicted": self.evicted,
            "blocked_attempts": self.blocked_attempts,
            "pressure_ticks": self.pressure_ticks,
        }


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

def _source_spec(scenario: Dict[str, Any]) -> Tuple[int, Optional[List[Any]], Optional[List[int]]]:
    """Return ``(count, payloads, priorities)`` for the injection source.

    ``items`` may be an integer (payloads are ``0..n-1``) or a list of values /
    ``{"value", "priority", "generation"}`` mappings.  ``priorities`` may be a
    list parallel to the items or a single default priority.
    """
    raw_items = scenario.get("items", 0)
    if isinstance(raw_items, list):
        payloads: Optional[List[Any]] = list(raw_items)
        count = len(payloads)
    else:
        payloads = None
        count = max(0, _int(raw_items, 0))

    raw_prio = scenario.get("priorities", scenario.get("item_priorities"))
    priorities: Optional[List[int]]
    if isinstance(raw_prio, list):
        priorities = [_int(p, 0) for p in raw_prio]
    elif raw_prio is None:
        priorities = None
    elif isinstance(raw_items, list):
        priorities = None
    else:
        priorities = [_int(raw_prio, 0)] * count
    return count, payloads, priorities


def _make_item(seq: int, payloads: Optional[List[Any]],
               priorities: Optional[List[int]], generation: int) -> Item:
    value: Any = seq
    priority = 0
    gen = generation
    if payloads is not None:
        raw = payloads[seq]
        if isinstance(raw, dict):
            value = raw.get("value", raw.get("id", seq))
            priority = _int(raw.get("priority", 0), 0)
            gen = _int(raw.get("generation", raw.get("epoch", generation)), generation)
        else:
            value = raw
    if priorities is not None and seq < len(priorities):
        priority = priorities[seq]
    return Item(seq=seq, value=value, priority=priority, generation=gen)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Run the simulation and return ``{"result", "metrics", "trace"}``.

    ``result`` is exactly the documented output document; ``metrics`` and
    ``trace`` carry the richer diagnostics used by the test-suite.
    """
    stages = _normalize_stages(scenario)
    names = [stage.name for stage in stages]
    levels: Dict[str, List[int]] = {name: [] for name in names}
    blocked_ticks: Dict[str, int] = {name: 0 for name in names}
    stalled_ticks: Dict[str, int] = {name: 0 for name in names}

    count, payloads, priorities = _source_spec(scenario)
    concurrency = max(1, _int(scenario.get("concurrency", 1), 1))
    max_ticks = max(1, _int(scenario.get("max_ticks", MAX_TICKS_DEFAULT), MAX_TICKS_DEFAULT))
    stalls = _normalize_stalls(scenario, stages)

    if not stages:
        result = {
            "dropped": 0,
            "deadlock": False,
            "water_levels": levels,
            "blocked_ticks": blocked_ticks,
            "throughput": 0,
            "order_ok": True,
            "ticks": 0,
        }
        return {"result": result, "metrics": {"stages": {}}, "trace": {"done": []}}

    buffers = [BoundedBuffer(stage) for stage in stages]
    done: List[Any] = []
    done_seq: List[int] = []
    done_gen: List[int] = []

    next_seq = 0
    source_blocked_ticks = 0
    tick = 0
    deadlock = False
    deadlock_tick: Optional[int] = None
    generation = 0
    generation_events: List[Tuple[int, int]] = []   # (tick, new generation)
    in_stall = False
    last_stall_end: Optional[int] = None
    forward_log: List[Tuple[int, int]] = []         # (tick, stage idx) of hand-offs
    disturbed: set = set()                          # stages the pause actually hit
    disturbed_at_end: set = set()                   # snapshot for the last pause

    while tick < max_ticks:
        # Finished when everything has been injected and every buffer is
        # drained; anything still buffered at max_ticks is reported as such.
        if next_seq >= count and not any(len(buf) for buf in buffers):
            break

        active_stall = any(stall.active(tick) for stall in stalls)
        if active_stall and not in_stall:
            # A stall opens a new epoch: items injected from now on belong to
            # the next generation.
            in_stall = True
            generation += 1
            generation_events.append((tick, generation))
            disturbed = set()
        elif not active_stall and in_stall:
            # Recovery closes it: everything admitted after this point carries
            # yet another generation.  Strict FIFO ordering guarantees that no
            # item of a later generation can overtake an item of an earlier
            # one, so the generations cannot interleave on the wire.
            in_stall = False
            generation += 1
            generation_events.append((tick, generation))
            last_stall_end = tick
            disturbed_at_end = set(disturbed)

        moved = False
        blocked_now = set()

        # 1) Injection.  A blocked source retains the item (retry channel);
        #    with the block policy nothing is ever discarded here.  The source
        #    is the producer upstream of the first stage, so its blocking is
        #    reported on that stage (and mirrored in the source metrics).
        injected = 0
        while injected < concurrency and next_seq < count:
            item = _make_item(next_seq, payloads, priorities, generation)
            outcome = buffers[0].offer(item)
            if outcome == BLOCKED:
                blocked_now.add(0)
                source_blocked_ticks += 1
                break
            # Rejection is metered inside the buffer itself.
            next_seq += 1
            injected += 1
            moved = True

        # 2) Forwarding.  Downstream admission is checked before an item is
        #    removed from its buffer, so a full downstream queue blocks the
        #    producer instead of dropping.  A stalled stage is skipped (its
        #    buffer still accepts, which is what builds the cascade) and the
        #    scheduler continues with the remaining stages: a slow consumer
        #    never halts unrelated queues.
        for i in range(len(stages)):
            if _is_stalled(stalls, i, tick):
                stalled_ticks[names[i]] += 1
                disturbed.add(i)
                continue
            buf = buffers[i]
            if not buf:
                continue
            item = buf.peek()
            if i + 1 == len(stages):
                buf.pop()
                done.append(item.value)
                done_seq.append(item.seq)
                done_gen.append(item.generation)
                forward_log.append((tick, i))
                moved = True
                continue
            target = buffers[i + 1]
            outcome = target.offer(item)
            if outcome == BLOCKED:
                blocked_now.add(i)
                continue
            # The item was consumed: accepted downstream (possibly after a
            # deliberate eviction, which the target meters itself) or dropped
            # by the target's rejection policy (also metered there).
            buf.pop()
            forward_log.append((tick, i))
            moved = True

        for i, name in enumerate(names):
            if buffers[i].pressure:
                buffers[i].pressure_ticks += 1

        # 3) Snapshot water levels once per tick, after every move.
        for i, name in enumerate(names):
            level = len(buffers[i])
            assert level <= stages[i].cap, "buffer %s exceeded cap" % name
            levels[name].append(level)

        tick += 1
        for i in blocked_now:
            blocked_ticks[names[i]] += 1
        if active_stall:
            # Every stage that felt the pause -- the stalled one and the
            # producers it backs up -- belongs to the recovery set.
            disturbed |= blocked_now

        pending = next_seq < count or any(len(buf) for buf in buffers)
        if not moved and pending:
            # Distinguish "everyone is waiting on the injected pause" from a
            # real wedge: ask (without mutating anything) whether the pipeline
            # could move at all if the pause were lifted.  A declared stall
            # therefore never raises a false positive, while a genuinely
            # unusable buffer is still reported even inside a stall window.
            upstream = None
            if next_seq < count:
                upstream = _make_item(next_seq, payloads, priorities, generation)
            if not _progress_possible(stages, buffers, upstream, stalls, tick):
                deadlock = True
                deadlock_tick = tick
                break

    # Recovery order is measured on real hand-off events (not on water-level
    # deltas): for each stage, the first tick after the pause in which it
    # successfully handed an item downstream.  The scheduler sweeps stages in
    # index order, so a stage with a full downstream buffer cannot move until
    # the tier below it has consumed, which yields the ordered, downstream ->
    # upstream resumption.  Only stages the pause actually disturbed are
    # listed: a tier that was never blocked has no recovery to report.
    recovery_first: Dict[str, Optional[int]] = {name: None for name in names}
    if last_stall_end is not None:
        for stage_tick, idx in forward_log:
            if stage_tick >= last_stall_end and recovery_first[names[idx]] is None:
                recovery_first[names[idx]] = stage_tick
    recovery_sequence = sorted(
        (recovery_first[names[idx]], names[idx])
        for idx in disturbed_at_end
        if recovery_first[names[idx]] is not None
    )

    order_ok = done_seq == sorted(done_seq) and _values_monotone(done)
    generations_ok = done_gen == sorted(done_gen)
    stages_metrics = {
        name: dict(
            buffers[i].snapshot(),
            blocked_ticks=blocked_ticks[name],
            stalled_ticks=stalled_ticks[name],
            max_level=max(levels[name]) if levels[name] else 0,
            mean_level=round(sum(levels[name]) / len(levels[name]), 3) if levels[name] else 0.0,
            first_drain_after_stall=recovery_first.get(name),
        )
        for i, name in enumerate(names)
    }
    # Rejections are metered by the buffer that refused the item, so the
    # documented top-level counter is the sum of the per-queue counters.
    dropped_total = sum(info["dropped"] for info in stages_metrics.values())
    result = {
        "dropped": dropped_total,
        "deadlock": deadlock,
        "water_levels": levels,
        "blocked_ticks": blocked_ticks,
        "throughput": len(done),
        "order_ok": bool(order_ok and generations_ok),
        "ticks": tick,
    }
    metrics = {
        "stages": stages_metrics,
        "source": {
            "injected": next_seq,
            "pending": count - next_seq,
            "blocked_ticks": source_blocked_ticks,
        },
        "deadlock_tick": deadlock_tick,
        "generation_events": generation_events,
        "generations": generation,
        "generation_order_ok": generations_ok,
        "recovery": {
            "stall_end": last_stall_end,
            "sequence": [name for _, name in recovery_sequence],
            "first_forward_tick": recovery_first,
        },
        "completed": len(done),
    }
    trace = {"done": list(done_seq), "values": list(done), "generations": list(done_gen)}
    return {"result": result, "metrics": metrics, "trace": trace}


def _values_monotone(values: Sequence[Any]) -> bool:
    """Order check for the documented integer payloads.

    Custom payloads may not be comparable; the sequence identifiers (checked
    separately) remain the authoritative ordering guarantee.
    """
    try:
        return list(values) == sorted(values)
    except TypeError:
        return True


def run(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Return the documented output document for ``scenario``."""
    sim = simulate(scenario)
    result = sim["result"]
    if scenario.get("include_metrics"):
        result = dict(result, metrics=sim["metrics"])
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        sys.stderr.write("usage: python3 pipeline.py scenario.json output.json\n")
        return 2
    with open(args[0], encoding="utf-8") as fh:
        scenario = json.load(fh)
    result = run(scenario)
    with open(args[1], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

