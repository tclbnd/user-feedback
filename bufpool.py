"""Buffer pool with LRU-K replacement, pin counts and write-ahead flushing.

Replacement policy (per the package rules):
  * Every fetch gets the next monotonic timestamp; a frame keeps all access
    timestamps since it was loaded.
  * The victim is the unpinned frame with the largest backward K-distance,
    i.e. current time minus the timestamp of its K-th most recent access.
  * Frames with fewer than K accesses have infinite distance; among them the
    frame whose first access is oldest is evicted first. One-shot scan pages
    therefore leave before pages touched K or more times.
  * A frame whose pin count is above zero is never a victim. If no frame can
    be evicted the fetch raises BufferFull immediately; it never waits.

Durability policy:
  * Before a dirty frame reaches data.db, the WAL is made durable up to and
    including the frame's page LSN (the LSN of its latest change).
  * A checkpoint's redo_lsn is the smallest rec_lsn among frames still dirty
    (rec_lsn is the LSN that first dirtied the frame), or the next LSN when
    nothing is dirty.
"""


class BufferFull(Exception):
    """Raised when a page must be loaded but no frame can be evicted."""


class Crash(Exception):
    """Raised by the crash injection point, right before a data page write."""


class Frame:
    __slots__ = ("pid", "lsn", "slots", "pins", "dirty", "rec_lsn", "history")

    def __init__(self, pid, lsn, slots):
        self.pid = pid
        self.lsn = lsn          # page LSN: LSN of the latest applied change
        self.slots = slots
        self.pins = 0           # pin count; > 0 means the frame cannot leave
        self.dirty = False
        self.rec_lsn = None     # LSN that first dirtied this frame
        self.history = []       # access timestamps, oldest first


class BufferPool:
    def __init__(self, disk, wal, frames, k=2, crash_at_write=None):
        if frames <= 0:
            raise ValueError("frames must be positive")
        if k <= 0:
            raise ValueError("k must be positive")
        self.disk = disk
        self.wal = wal
        self.capacity = frames
        self.k = k
        self.frames = {}        # pid -> Frame
        self.clock = 0          # monotonic access timestamp
        self.writes = 0         # data page write-back attempts (for injection)
        self.crash_at_write = crash_at_write
        self.stats = {
            "hits": 0,
            "misses": 0,
            "evictions": [],
            "checkpoints": [],
        }

    # ------------------------------------------------------------------ #
    # LRU-K victim selection
    # ------------------------------------------------------------------ #
    def _rank(self, frame):
        """Sort key: smaller keys are evicted first.

        Group 0: fewer than K accesses (infinite backward K-distance),
                 ordered by oldest first access.
        Group 1: at least K accesses, ordered by the timestamp of the
                 K-th most recent access; the smaller that timestamp the
                 larger the backward K-distance, so it goes first.
        """
        if len(frame.history) < self.k:
            return (0, frame.history[0])
        kth_most_recent = frame.history[-self.k]
        return (1, kth_most_recent)

    def victim(self):
        """Return the frame to evict, or None when every frame is pinned."""
        candidates = (f for f in self.frames.values() if f.pins == 0)
        return min(candidates, key=self._rank, default=None)

    # ------------------------------------------------------------------ #
    # Fetch / pin / unpin
    # ------------------------------------------------------------------ #
    def fetch(self, pid):
        self.clock += 1  # every fetch is one access with the next timestamp
        frame = self.frames.get(pid)
        if frame is not None:
            self.stats["hits"] += 1
        else:
            if len(self.frames) >= self.capacity:
                old = self.victim()
                if old is None:
                    # Every frame is pinned: fail this operation at once.
                    # No busy waiting, no flush loop: pinned dirty frames
                    # cannot be written back anyway. No frame is installed,
                    # so this failed fetch counts as neither hit nor miss.
                    raise BufferFull(pid)
                self._evict(old)
            frame = Frame(pid, *self.disk.read(pid))
            self.frames[pid] = frame
            self.stats["misses"] += 1
        frame.history.append(self.clock)
        frame.pins += 1
        return frame

    def unpin(self, pid):
        frame = self.frames.get(pid)
        if frame is None or frame.pins == 0:
            return False
        frame.pins -= 1
        return True

    # ------------------------------------------------------------------ #
    # Logged modification
    # ------------------------------------------------------------------ #
    def modify(self, pid, slot, value):
        frame = self.fetch(pid)
        lsn = self.wal.append({"page": pid, "slot": slot, "value": value})
        frame.slots[slot] = value
        frame.lsn = lsn
        if not frame.dirty:
            # First change since the frame was (last) made clean: remember
            # the LSN that first dirties it for checkpoint advancement.
            frame.dirty = True
            frame.rec_lsn = lsn
        self.unpin(pid)
        return lsn

    # ------------------------------------------------------------------ #
    # Write-back: WAL first, data page second
    # ------------------------------------------------------------------ #
    def write_back(self, frame):
        if not frame.dirty:
            return
        # Write-ahead logging: the log record of the latest change must be
        # durable before the data page is overwritten on disk.
        self.wal.flush(frame.lsn)
        self.writes += 1
        if self.crash_at_write is not None and self.writes >= self.crash_at_write:
            # Crash injection point: the WAL up to frame.lsn is already
            # durable, and this data page has not been written yet.
            raise Crash()
        self.disk.write(frame.pid, frame.lsn, frame.slots)
        frame.dirty = False
        frame.rec_lsn = None

    def _evict(self, frame):
        self.write_back(frame)
        del self.frames[frame.pid]
        self.stats["evictions"].append(frame.pid)

    def flush_some(self, limit):
        """Background writer: up to `limit` unpinned dirty frames, oldest
        first modification (smallest rec_lsn) first."""
        dirty = [f for f in self.frames.values() if f.dirty and f.pins == 0]
        dirty.sort(key=lambda f: f.rec_lsn)
        for frame in dirty[:limit]:
            self.write_back(frame)

    # ------------------------------------------------------------------ #
    # Checkpoint
    # ------------------------------------------------------------------ #
    def checkpoint(self):
        # redo_lsn is the smallest LSN that first dirtied a frame still
        # dirty; when nothing is dirty, redo starts at the next LSN.
        recs = [f.rec_lsn for f in self.frames.values() if f.dirty]
        redo = min(recs) if recs else self.wal.next_lsn
        self.wal.append({"type": "checkpoint", "redo_lsn": redo})
        self.wal.flush()
        self.stats["checkpoints"].append(redo)
        return redo
