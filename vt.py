#!/usr/bin/env python3
"""Virtual-texture page-table residency planner.

Usage
-----
    python3 vt.py req.json output.json [--threads N] [--chunk K] [--hysteresis H]

Input format (see README)::

    {"pages": 256, "phys": 64, "bw": 8,
     "frames": [[{"pid": 12, "mip": 0, "area": 0.5}, ...], ...]}

Output format::

    {"resident": [[pid, ...], ...], "faults": [n, ...], "digest": "<sha256>"}

Policy (deterministic reference):

* every frame's records are coalesced and sorted by
  (mip ascending, area descending, pid ascending) before any state is
  touched, so replay order, request chunking and worker count cannot
  change the result;
* the priority queue is served from the front, so the best ``phys``
  requests of a frame form the target residency set; a page that is
  already resident is only touched, never reloaded;
* at most ``bw`` pages are paged in per frame; anything beyond the budget
  keeps its old (coarser mip) mapping;
* cross-frame hysteresis keeps recently requested pages for a few extra
  frames so alternating requests do not reload the same page every frame;
  such pages are evicted before a page the current frame asked for;
* eviction prefers dropping fine detail before the coarse backbone, and a
  page that currently backs a live fallback entry is never recycled, so
  the "fall back to the upper mip" mapping stays valid;
* page-table update and physical-page recycling are two separately
  committed transactions per frame: the install transaction commits the
  new mappings, only afterwards the reclaim transaction drops the
  replaced mappings and returns their slots.  During the transition both
  the old and the new mapping are addressable, so a request never lands
  on a stale or half-freed slot.

Requests may carry an optional ``parent`` (pid or {"pid":..,"mip":..});
otherwise the square-map pyramid relation is derived from ``pages``.
"""

import argparse
import hashlib
import heapq
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

DEFAULT_HYSTERESIS = 1
MAX_MIP_DEPTH = 24


class RequestError(ValueError):
    """Malformed input request file."""


def _to_int(value, what):
    if isinstance(value, bool):
        raise RequestError("%s must be an integer, got %r" % (what, value))
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise RequestError("%s must be an integer, got %r" % (what, value))
    if isinstance(value, float) and not value.is_integer():
        raise RequestError("%s must be an integer, got %r" % (what, value))
    return number


def _to_float(value, what):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise RequestError("%s must be a number, got %r" % (what, value))
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def normalize_frame(raw, chunk=0):
    """Canonical request queue of one frame.

    Shared records coalesce to the strongest request for a pid (lowest
    mip, then largest area, then smallest pid as a pure tiebreak).  The
    queue is sorted by (mip asc, area desc, pid asc) so the outcome is
    independent of record order, chunk boundaries and worker assignment.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise RequestError("frame must be a list of request objects")

    best = {}  # pid -> (mip, area, count)

    def consume(records):
        for record in records:
            if not isinstance(record, dict):
                raise RequestError("request must be an object, got %r" % (record,))
            pid = _to_int(record.get("pid"), "pid")
            if pid < 0:
                raise RequestError("pid must be >= 0, got %d" % pid)
            mip = _to_int(record.get("mip", 0), "mip")
            if mip < 0:
                mip = 0
            area = _to_float(record.get("area", 0.0), "area")
            if area < 0.0:
                area = 0.0
            previous = best.get(pid)
            if previous is None:
                best[pid] = (mip, area, 1)
            else:
                prev_mip, prev_area, prev_count = previous
                if (mip, -area) < (prev_mip, -prev_area):
                    best[pid] = (mip, area, prev_count + 1)
                else:
                    best[pid] = (prev_mip, prev_area, prev_count + 1)

    try:
        size = int(chunk or 0)
    except (TypeError, ValueError):
        size = 0
    if size > 0:
        for start in range(0, len(raw), size):
            consume(raw[start:start + size])
    else:
        consume(raw)

    return sorted(
        ((pid, mip, area, count) for pid, (mip, area, count) in best.items()),
        key=lambda item: (item[1], -item[2], item[0]),
    )


def normalize_frames(raw_frames, chunk=0, threads=1):
    """Canonicalise all frames, optionally with a worker pool.

    Only the pure canonicalisation step is parallel; the stateful
    residency simulation stays sequential and frame ordered, and
    ThreadPoolExecutor.map preserves input order, so the worker count is
    unobservable in the output.
    """
    if not isinstance(raw_frames, list):
        raise RequestError("frames must be a list")
    try:
        workers = int(threads or 1)
    except (TypeError, ValueError):
        workers = 1
    workers = max(1, min(workers, max(1, len(raw_frames))))
    if workers == 1:
        return [normalize_frame(frame, chunk) for frame in raw_frames]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda frame: normalize_frame(frame, chunk), raw_frames))


def scan_parent_hints(raw_frames):
    """Collect optional explicit parent hints from the request records.

    If the same pid carries conflicting hints, the canonically smallest
    ``(parent_pid, parent_mip)`` wins, so the choice cannot depend on
    record order.
    """
    hints = {}
    for frame in raw_frames if isinstance(raw_frames, list) else []:
        if not isinstance(frame, list):
            continue
        for record in frame:
            if not isinstance(record, dict):
                continue
            try:
                pid = int(record.get("pid"))
            except (TypeError, ValueError, OverflowError):
                continue
            parent = record.get("parent")
            if parent is None:
                continue
            try:
                if isinstance(parent, dict):
                    parent_pid = int(parent.get("pid"))
                    parent_mip = parent.get("mip")
                    parent_mip = int(parent_mip) if parent_mip is not None else None
                else:
                    parent_pid = int(parent)
                    parent_mip = None
            except (TypeError, ValueError, OverflowError):
                continue
            if parent_pid < 0:
                continue
            hint = (parent_pid, parent_mip)
            previous = hints.get(pid)
            if previous is None:
                hints[pid] = hint
                continue
            # None means "any level"; sort it after concrete levels
            previous_key = (previous[0], -1 if previous[1] is None else previous[1])
            hint_key = (hint[0], -1 if hint[1] is None else hint[1])
            if hint_key < previous_key:
                hints[pid] = hint
    return hints


class MipPyramid:
    """Coarse mip ancestor lookup.

    The default layout is a square virtual texture: each mip level halves
    the tile count per axis, so a tile's parent is the tile covering it
    one level above.  Non-square ``pages`` fall back to the quadtree index
    relation ``pid >> 2``.  Explicit ``parent`` hints take precedence.
    """

    def __init__(self, pages, hints=None):
        self.pages = max(1, int(pages))
        self.hints = hints or {}
        width = int(round(math.sqrt(self.pages)))
        self._base_width = width if width * width == self.pages else None

    def _tile_count(self, mip):
        if mip < 0:
            mip = 0
        return max(1, self.pages >> (2 * mip))

    def parent(self, pid, mip):
        hint = self.hints.get(pid)
        if hint is not None:
            return hint
        if self._tile_count(mip) <= 1:
            return None
        parent_pid = pid >> 2
        if self._base_width is None or pid >= self._tile_count(mip) or parent_pid == pid:
            return (parent_pid, mip + 1) if parent_pid != pid else None
        width = self._base_width >> mip
        if width <= 1:
            return None
        row, col = divmod(pid, width)
        parent_width = width >> 1
        if parent_width <= 0:
            return None
        return ((row >> 1) * parent_width + (col >> 1), mip + 1)

    def ancestors(self, pid, mip):
        """Yield (ancestor_pid, expected_mip, depth) up the pyramid."""
        current = (pid, mip)
        seen = {current}
        for depth in range(1, MAX_MIP_DEPTH + 1):
            parent = self.parent(current[0], current[1])
            if parent is None or parent in seen:
                return
            seen.add(parent)
            yield parent[0], parent[1], depth
            current = (parent[0], parent[1] if parent[1] is not None else current[1] + 1)


@dataclass
class Page:
    """One page-table entry.

    ``direct`` entries own a physical slot.  ``fallback`` entries have no
    slot and are served through a resident coarse-mip ancestor stored in
    ``owner``; the entry is kept addressable until hysteresis drops it.
    """

    pid: int
    mip: int
    area: float
    slot: Optional[int] = None
    owner: Optional[int] = None
    direct: bool = False
    last_request: int = -1
    last_touch: int = -1
    pending: bool = False

    @property
    def kind(self):
        if self.direct:
            return "resident"
        return "fallback" if self.owner is not None else "missing"


class ResidencyPlanner:
    """Stateful planner; one instance processes one camera path.

    Per frame the planner runs three ordered phases:

    1. *queue*: canonical priority order, touch resident pages, plan
       installs and pick victims for slot hand-off;
    2. *install*: commit the new page-table mappings (victims keep their
       old mapping and slot until this phase ends);
    3. *reclaim*: drop replaced/expired entries, return slots, and
       re-validate every fallback entry against the mip pyramid so its
       ancestor mapping stays valid.
    """

    def __init__(self, capacity, bandwidth, hysteresis, pyramid):
        self.capacity = max(0, int(capacity))
        self.bandwidth = max(0, int(bandwidth))
        self.hysteresis = max(0, int(hysteresis))
        self.pyramid = pyramid
        self.table = {}
        self.free_slots = []
        self.next_slot = 0
        self.resident_out = []
        self.faults_out = []
        self.summary_out = []

    # -- physical allocator -------------------------------------------------
    def _take_slot(self):
        if self.free_slots:
            return heapq.heappop(self.free_slots)
        if self.next_slot < self.capacity:
            slot = self.next_slot
            self.next_slot += 1
            return slot
        return None

    def _free_slot(self, slot):
        if slot is not None and slot < self.next_slot:
            heapq.heappush(self.free_slots, slot)

    # -- mip pyramid -----------------------------------------------------
    def _nearest_ancestor(self, pid, mip):
        for ancestor_pid, expected_mip, _depth in self.pyramid.ancestors(pid, mip):
            page = self.table.get(ancestor_pid)
            if page is None or not page.direct:
                continue
            if expected_mip is not None and page.mip != expected_mip:
                continue
            return ancestor_pid
        return None

    def _pinned_owners(self):
        """Coarse pages that currently back a fallback entry."""
        pinned = set()
        for page in self.table.values():
            if not page.direct and page.owner is not None:
                pinned.add(page.owner)
        return pinned

    def _needed_roots(self, records):
        """Coarse pages that must stay resident to serve this frame.

        Every requested page that is not itself resident is served through
        the closest resident ancestor.  Those ancestors are protected from
        eviction for the whole frame so the fallback mapping cannot go
        stale between the install and reclaim phases.
        """
        roots = set()
        for pid, mip, _area, _count in records:
            page = self.table.get(pid)
            if page is not None and page.direct:
                continue
            owner = self._nearest_ancestor(pid, mip)
            if owner is not None:
                roots.add(owner)
        return roots

    def _pick_victim(self, wanted, target, pinned, protected):
        best = None
        best_key = None
        for page in self.table.values():
            if not page.direct or page.pending or page.pid in target:
                continue
            if page.pid in pinned and page.pid not in wanted:
                # Hard invariant: a page that backs a live fallback entry
                # is never recycled while that entry still needs it.
                continue
            key = (
                1 if page.pid in protected else 0,  # fallback roots go last
                1 if page.pid not in wanted else 0,
                page.last_touch,
                page.mip,          # coarse backbone first, fine detail last
                page.area,
                -page.pid,
            )
            if best_key is None or key < best_key:
                best_key = key
                best = page
        return best

    # -- frame -------------------------------------------------------------
    def step(self, frame, records):
        wanted = set(record[0] for record in records)
        target = set(record[0] for record in records[:self.capacity])
        pinned = self._pinned_owners()
        protected_roots = pinned | self._needed_roots(records)

        handoffs = []      # victims whose slot is handed to a new page
        expired = []       # entries dropped by hysteresis
        loads = 0

        for pid, mip, area, _count in records:
            page = self.table.get(pid)
            if page is not None:
                page.last_request = frame
                page.mip = mip
                page.area = area
                if page.direct:
                    page.last_touch = frame
                    continue
            else:
                page = Page(pid=pid, mip=mip, area=area, last_request=frame)
                page.last_touch = frame
                self.table[pid] = page

            if pid not in target or loads >= self.bandwidth or self.capacity <= 0:
                continue

            slot = self._take_slot()
            if slot is None:
                victim = self._pick_victim(wanted, target, pinned, protected_roots)
                if victim is None:
                    continue
                slot = victim.slot
                victim.pending = True
                handoffs.append(victim)

            # Phase 1 (install): the new mapping is committed here, while
            # the victim mapping/slot stays registered until phase 3.
            page.slot = slot
            page.direct = True
            page.owner = pid
            page.last_touch = frame
            page.last_request = frame
            loads += 1

        # Hysteresis: a page keeps its entry for ``hysteresis`` extra
        # frames after its last request, which damps frame-to-frame
        # oscillation of the same page.
        for page in self.table.values():
            if page.pending or page.pid in wanted:
                continue
            if frame - page.last_request > self.hysteresis:
                page.pending = True
                expired.append(page)

        self._phase_reclaim(handoffs, expired)

        self.resident_out.append(self._resident_pids())
        self.faults_out.append(loads)
        self.summary_out.append(self._frame_summary())

    def _phase_reclaim(self, handoffs, expired):
        # Phase 3: release mappings.  Hand-off victims are demoted to
        # fallback entries (their slot already belongs to the new page),
        # expired entries are removed and their slots returned.
        for victim in handoffs:
            victim.direct = False
            victim.slot = None
            victim.pending = False
            victim.owner = None

        for page in expired:
            page.pending = False
            if page.direct:
                self._free_slot(page.slot)
            if self.table.get(page.pid) is page:
                del self.table[page.pid]

        # Re-validate fallback entries so every one of them points at a
        # resident coarse-mip ancestor; owners are never dropped silently.
        for page in self.table.values():
            if page.direct:
                page.owner = page.pid
                continue
            if page.owner is not None:
                owner = self.table.get(page.owner)
                if owner is not None and owner.direct:
                    continue
            page.owner = self._nearest_ancestor(page.pid, page.mip)

    def _resident_pids(self):
        return sorted(page.pid for page in self.table.values() if page.direct)

    def resolve(self, pid, mip):
        """Public helper: (kind, slot, owner) for a page-table lookup."""
        page = self.table.get(pid)
        if page is not None and page.direct:
            return "resident", page.slot, pid
        owner = self._nearest_ancestor(pid, mip)
        if owner is None:
            return "missing", None, None
        return "fallback", self.table[owner].slot, owner

    def _frame_summary(self):
        entries = []
        for page in sorted(self.table.values(), key=lambda item: item.pid):
            entries.append([page.pid, page.mip, page.kind, page.slot, page.owner])
        return {
            "entries": entries,
            "resident": self._resident_pids(),
        }


def plan(request, chunk=0, threads=1, hysteresis=DEFAULT_HYSTERESIS):
    if not isinstance(request, dict):
        raise RequestError("request must be an object")
    pages = _to_int(request.get("pages", 0), "pages")
    phys = _to_int(request.get("phys", 0), "phys")
    bandwidth = _to_int(request.get("bw", 0), "bw")
    raw_frames = request.get("frames", [])
    if raw_frames is None:
        raw_frames = []
    if not isinstance(raw_frames, list):
        raise RequestError("frames must be a list")

    prepared = normalize_frames(raw_frames, chunk=chunk, threads=threads)
    planner = ResidencyPlanner(phys, bandwidth, hysteresis,
                               MipPyramid(pages, scan_parent_hints(raw_frames)))
    for frame_index, records in enumerate(prepared):
        planner.step(frame_index, records)

    canonical = {
        "pages": pages,
        "phys": phys,
        "bw": bandwidth,
        "hysteresis": max(0, int(hysteresis)),
        "frames": planner.summary_out,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return {
        "resident": planner.resident_out,
        "faults": planner.faults_out,
        "digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def run(req):
    return plan(req)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Virtual-texture page-table residency planner")
    parser.add_argument("input", help="request JSON file (see README)")
    parser.add_argument("output", help="output JSON file")
    parser.add_argument("--threads", type=int, default=1,
                        help="normalisation worker threads (output is order invariant)")
    parser.add_argument("--chunk", type=int, default=0,
                        help="normalisation chunk size, 0 = whole frame at once")
    parser.add_argument("--hysteresis", type=int,
                        default=int(os.environ.get("VT_HYSTERESIS", DEFAULT_HYSTERESIS)),
                        help="extra frames a recently requested page stays resident")
    args = parser.parse_args(argv)
    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            request = json.load(handle)
        result = plan(request, chunk=args.chunk, threads=args.threads,
                      hysteresis=args.hysteresis)
    except (OSError, ValueError) as exc:
        print("vt.py: %s" % exc, file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
