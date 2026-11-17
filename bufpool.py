"""Buffer pool with LRU-K replacement, pin counts and write-ahead flushing."""


class BufferFull(Exception):
    pass


class Crash(Exception):
    pass


class Frame:
    def __init__(self, pid, lsn, slots):
        self.pid, self.lsn, self.slots = pid, lsn, slots
        self.pins, self.dirty, self.rec_lsn, self.history = 0, False, None, []


class BufferPool:
    def __init__(self, disk, wal, frames, k=2, crash_at_write=None):
        self.disk, self.wal, self.capacity, self.k = disk, wal, frames, k
        self.frames, self.clock, self.writes = {}, 0, 0
        self.crash_at_write = crash_at_write
        self.stats = {"hits": 0, "misses": 0, "evictions": [], "checkpoints": []}

    def victim(self):
        """Unpinned frame that was used least recently."""
        best, best_key = None, None
        for frame in self.frames.values():
            if frame.pins:
                continue
            key = frame.history[-1]
            if best_key is None or key < best_key:
                best, best_key = frame, key
        return best

    def fetch(self, pid):
        self.clock += 1
        frame = self.frames.get(pid)
        if frame:
            self.stats["hits"] += 1
        else:
            if len(self.frames) >= self.capacity:
                old = self.victim()
                while old is None:
                    self.flush_some(len(self.frames))
                    old = self.victim()
                self.write_back(old)
                del self.frames[old.pid]
                self.stats["evictions"].append(old.pid)
            frame = self.frames[pid] = Frame(pid, *self.disk.read(pid))
            self.stats["misses"] += 1
        frame.history.append(self.clock)
        frame.pins += 1
        return frame

    def unpin(self, pid):
        frame = self.frames.get(pid)
        if frame is None or frame.pins == 0:
            return False
        frame.pins = 0
        return True

    def modify(self, pid, slot, value):
        frame = self.fetch(pid)
        lsn = self.wal.append({"page": pid, "slot": slot, "value": value})
        frame.slots[slot], frame.lsn = value, lsn
        if not frame.dirty:
            frame.dirty, frame.rec_lsn = True, lsn
        self.unpin(pid)

    def write_back(self, frame):
        if not frame.dirty:
            return
        self.wal.flush(frame.lsn)
        self.writes += 1
        if self.crash_at_write is not None and self.writes >= self.crash_at_write:
            raise Crash()
        self.disk.write(frame.pid, frame.lsn, frame.slots)
        frame.dirty, frame.rec_lsn = False, None

    def flush_some(self, limit):
        dirty = sorted((f for f in self.frames.values() if f.dirty and not f.pins), key=lambda f: f.rec_lsn)
        for frame in dirty[:limit]:
            self.write_back(frame)

    def checkpoint(self):
        recs = [f.lsn for f in self.frames.values() if f.dirty]
        redo = min(recs) if recs else self.wal.next_lsn
        self.wal.append({"type": "checkpoint", "redo_lsn": redo})
        self.wal.flush()
        self.stats["checkpoints"].append(redo)
