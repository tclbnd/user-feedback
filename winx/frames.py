"""Window frames.

A window: {"partition_by": [cols], "order_by": [{"col", "dir"}],
           "frame": {"mode": "rows"|"range"|"groups",
                     "start": {"type": "unbounded_preceding"|"preceding"|"current_row"|"following"|"unbounded_following", "offset": n},
                     "end": {...}, "exclude": "no_others"|"current_row"|"group"|"ties"} or null}
With no frame clause the SQL default applies.

Boundary rules follow PostgreSQL:

* ROWS counts physical rows, RANGE counts value distances (offset bounds need
  exactly one ORDER BY column) and GROUPS counts peer groups.
* CURRENT ROW is "this row" in ROWS mode but the edge of the current peer
  group in RANGE and GROUPS mode.
* Peers are rows equal on the whole ORDER BY list; NULLs are peers of each
  other.  With no ORDER BY every row of the partition is a peer.
* Boundaries outside the partition are clamped; if the start boundary ends up
  after the end boundary the frame is empty.
* EXCLUDE CURRENT ROW/GROUP/TIES remove rows after the bounds are computed.
"""


def default_frame(window):
    """The frame used when no frame clause is written.

    PostgreSQL's default is ``RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT
    ROW``.  Without ORDER BY every row is a peer, so this covers the whole
    partition (the default for a window without ORDER BY as well).
    """
    return {"mode": "range",
            "start": {"type": "unbounded_preceding"},
            "end": {"type": "current_row"},
            "exclude": "no_others"}


def _peer_info(keys):
    """Peer group metadata for an ordered partition.

    Returns (peer_lo, peer_hi, group_id, group_starts, group_ends): the
    first/last index of each row's peer group and, per group number, the
    first/last index of that group.
    """
    n = len(keys)
    peer_lo = [0] * n
    peer_hi = [0] * n
    group_id = [0] * n
    starts = []
    ends = []
    g = 0
    j = 0
    while j < n:
        k = j
        while k + 1 < n and keys[k + 1] == keys[j]:
            k += 1
        for t in range(j, k + 1):
            peer_lo[t] = j
            peer_hi[t] = k
            group_id[t] = g
        starts.append(j)
        ends.append(k)
        g += 1
        j = k + 1
    return peer_lo, peer_hi, group_id, starts, ends


def _cmp_values(a, b, desc):
    """Compare ordering values the way the window ORDER BY does.

    ASC sorts NULLs last, DESC sorts them first (PostgreSQL defaults).
    """
    if a is None and b is None:
        return 0
    if a is None:
        return -1 if desc else 1
    if b is None:
        return 1 if desc else -1
    if a == b:
        return 0
    c = -1 if a < b else 1
    return -c if desc else c


def _first_above(values, threshold, desc):
    """First index whose value is >= threshold in window order (or len)."""
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if _cmp_values(values[mid], threshold, desc) < 0:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _last_at_or_below(values, threshold, desc):
    """Last index whose value is <= threshold in window order (or -1)."""
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if _cmp_values(values[mid], threshold, desc) <= 0:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def _is_offset(bound):
    return bound["type"] in ("preceding", "following")


_BOUND_TYPES = {"unbounded_preceding", "preceding", "current_row",
                "following", "unbounded_following"}


def _check_bound(bound, which):
    t = bound.get("type")
    if t not in _BOUND_TYPES:
        raise ValueError("unknown frame bound: %r" % (t,))
    if t in ("preceding", "following") and bound.get("offset", 0) < 0:
        raise ValueError("frame %s offset must not be negative" % (which,))


def _check_frame(start, end):
    """Reject the frame definitions PostgreSQL rejects at parse time.

    Without this the mode-specific position helpers would silently produce a
    nonsensical (usually empty) frame instead of the error PostgreSQL raises.
    """
    _check_bound(start, "starting")
    _check_bound(end, "ending")
    st, et = start["type"], end["type"]
    if st == "unbounded_following":
        raise ValueError("frame start cannot be UNBOUNDED FOLLOWING")
    if et == "unbounded_preceding":
        raise ValueError("frame end cannot be UNBOUNDED PRECEDING")
    if st == "current_row" and et in ("preceding", "unbounded_preceding"):
        raise ValueError("frame starting from current row cannot have preceding rows")
    if st == "following":
        if et in ("preceding", "unbounded_preceding"):
            raise ValueError("frame starting from following row cannot have preceding rows")
        if et == "current_row":
            raise ValueError("frame starting from following row cannot end with current row")


class FrameEvaluator(object):
    """The frame of each row of one ordered partition of one window.

    Peer groups, the RANGE ordering values and the frame definition are
    computed once, then ``rows(i)`` resolves row i's frame in O(1) (ROWS and
    GROUPS) or O(log n) (RANGE offsets).
    """

    def __init__(self, window, part, keys):
        self.frame = window.get("frame") or default_frame(window)
        self.mode = (self.frame.get("mode") or "range").lower()
        self.start = self.frame["start"]
        self.end = self.frame["end"]
        self.exclude = self.frame.get("exclude", "no_others")
        _check_frame(self.start, self.end)
        self.n = len(part)
        (self.peer_lo, self.peer_hi, self.group_id,
         self.group_starts, self.group_ends) = _peer_info(keys)

        order = window.get("order_by") or []
        self.values = None
        self.desc = False
        if self.mode == "rows":
            pass
        elif self.mode == "range":
            if _is_offset(self.start) or _is_offset(self.end):
                if len(order) != 1:
                    raise ValueError(
                        "RANGE with an offset requires exactly one ORDER BY column")
                self.desc = order[0].get("dir", "asc") == "desc"
                self.values = [r.get(order[0]["col"]) for r in part]
        elif self.mode == "groups":
            # PostgreSQL rejects GROUPS mode without an ORDER BY clause:
            # peer groups are defined by the ordering values.
            if not order:
                raise ValueError("GROUPS mode requires an ORDER BY clause")
        else:
            raise ValueError("unknown frame mode: %r" % (self.mode,))

    # -- boundary positions ------------------------------------------------
    def _rows_pos(self, bound, i):
        t = bound["type"]
        if t == "unbounded_preceding":
            return 0
        if t == "unbounded_following":
            return self.n - 1
        if t == "current_row":
            return i
        off = bound.get("offset", 0)
        if t == "preceding":
            return i - off
        if t == "following":
            return i + off
        raise ValueError("unknown frame bound: %r" % (t,))

    def _range_pos(self, bound, i, is_start):
        t = bound["type"]
        if t == "unbounded_preceding":
            return 0
        if t == "unbounded_following":
            return self.n - 1
        if t == "current_row":
            return self.peer_lo[i] if is_start else self.peer_hi[i]
        off = bound.get("offset", 0)
        cur = self.values[i]
        if cur is None:
            # A NULL ordering value has no numeric distance: its offsets stay
            # on the edge of the current NULL peer group.
            return self.peer_lo[i] if is_start else self.peer_hi[i]
        # PRECEDING walks backwards in ordering direction: +offset for DESC,
        # -offset for ASC.  FOLLOWING is the reverse.  NULLs take part in the
        # comparison at the far end of the ordering, like PostgreSQL.
        sign = -1 if t == "preceding" else 1
        if self.desc:
            sign = -sign
        threshold = cur + sign * off
        if is_start:
            return _first_above(self.values, threshold, self.desc)
        return _last_at_or_below(self.values, threshold, self.desc)

    def _groups_pos(self, bound, i, is_start):
        t = bound["type"]
        if t == "unbounded_preceding":
            return 0
        if t == "unbounded_following":
            return self.n - 1
        if t == "current_row":
            g = self.group_id[i]
            return self.group_starts[g] if is_start else self.group_ends[g]
        off = bound.get("offset", 0)
        g = self.group_id[i]
        g = g - off if t == "preceding" else g + off
        count = len(self.group_starts)
        if is_start:
            if g < 0:
                return 0
            if g >= count:
                return self.n
            return self.group_starts[g]
        if g < 0:
            return -1
        if g >= count:
            return self.n - 1
        return self.group_ends[g]

    # -- membership --------------------------------------------------------
    def rows(self, i):
        """Indexes (into the ordered partition) of the rows in row i's frame."""
        if self.mode == "rows":
            lo = self._rows_pos(self.start, i)
            hi = self._rows_pos(self.end, i)
        elif self.mode == "range":
            lo = self._range_pos(self.start, i, True)
            hi = self._range_pos(self.end, i, False)
        else:
            lo = self._groups_pos(self.start, i, True)
            hi = self._groups_pos(self.end, i, False)

        # Clamp to the partition; the frame is empty when the start boundary
        # lies after the end boundary.
        lo = max(lo, 0)
        hi = min(hi, self.n - 1)
        if lo > hi:
            return []
        rows = list(range(lo, hi + 1))

        exclude = self.exclude
        if exclude in (None, "no_others"):
            pass
        elif exclude == "current_row":
            rows = [j for j in rows if j != i]
        elif exclude == "group":
            peer_lo, peer_hi = self.peer_lo[i], self.peer_hi[i]
            rows = [j for j in rows if not (peer_lo <= j <= peer_hi)]
        elif exclude == "ties":
            peer_lo, peer_hi = self.peer_lo[i], self.peer_hi[i]
            rows = [j for j in rows if j == i or not (peer_lo <= j <= peer_hi)]
        else:
            raise ValueError("unknown frame exclusion: %r" % (exclude,))
        return rows


def frame_rows(window, part, i, keys):
    """Indexes (into the ordered partition) of the rows in row i's frame."""
    return FrameEvaluator(window, part, keys).rows(i)
