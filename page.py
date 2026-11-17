"""Slotted data page."""
import struct

SIZE, HDR = 256, 6
FRAGMENT_LIMIT = SIZE // 2
SLACK = 4       # one slot directory entry
RESERVE = 5     # room for an `F` record, so any row can always be forwarded


class Page:
    def __init__(self, raw=None):
        self.raw = bytearray(raw) if raw else bytearray(b"D\0" + struct.pack(">HH", 0, SIZE) + bytes(SIZE - HDR))

    @property
    def nslots(self):
        return struct.unpack_from(">H", self.raw, 2)[0]

    def slot(self, i):
        return struct.unpack_from(">HH", self.raw, HDR + 4 * i)

    def set_slot(self, i, off, length):
        struct.pack_into(">HH", self.raw, HDR + 4 * i, off, length)

    def record(self, i):
        """Bytes of slot `i`, or None when the slot is free/out of range."""
        if not 0 <= i < self.nslots:
            return None
        off, length = self.slot(i)
        return bytes(self.raw[off:off + length]) if length else None

    def free(self):
        used = sum(self.slot(i)[1] for i in range(self.nslots))
        return SIZE - HDR - 4 * self.nslots - used

    def contiguous(self):
        return struct.unpack_from(">H", self.raw, 4)[0] - HDR - 4 * self.nslots

    def free_slot(self):
        """Lowest slot with no live record, or None."""
        for i in range(self.nslots):
            if self.slot(i)[1] == 0:
                return i
        return None

    def live_lengths(self, skip=None):
        return [self.slot(i)[1] for i in range(self.nslots)
                if self.slot(i)[1] and i != skip]

    def reserve(self, skip=None, size=None):
        """Bytes that must stay free so every live slot could become an `F` record.

        A forwarding pointer is always 5 bytes.  A record shorter than that
        cannot be overwritten in place by a pointer unless the page holds the
        difference, and every such row may need to move eventually, so the page
        keeps the *sum* of those differences free.
        """
        lengths = self.live_lengths(skip=skip)
        if size is not None:
            lengths.append(size)
        return sum(max(0, RESERVE - n) for n in lengths)

    def fits(self, size, reuse=None):
        """Can a record of `size` bytes be stored here?

        `reuse` names an existing slot; its old record may be overwritten, so
        only the offset matters, not its old length.  A brand-new slot costs 4
        more bytes of directory.  The page must also keep the forwarding reserve
        described in `reserve()`.
        """
        old = self.slot(reuse)[1] if reuse is not None else 0
        nslots = self.nslots + (1 if reuse is None else 0)
        used = sum(l for l in self.live_lengths()) - old + size
        free_after = SIZE - HDR - SLACK * nslots - used
        return free_after >= self.reserve(skip=reuse, size=size)

    def compact(self):
        """Pack live records at the end of the page, keeping every slot number.

        Deleted slots stay deleted and slot numbers never shift, so row ids
        survive compaction.
        """
        recs = [(i, self.record(i)) for i in range(self.nslots) if self.slot(i)[1]]
        for i in range(self.nslots):
            self.set_slot(i, 0, 0)
        end = SIZE
        for i, rec in recs:
            end -= len(rec)
            self.raw[end:end + len(rec)] = rec
            self.set_slot(i, end, len(rec))
        struct.pack_into(">H", self.raw, 4, end)

    def maybe_compact(self):
        """Compact once holes exceed the fragmentation threshold."""
        if self.free() - self.contiguous() > FRAGMENT_LIMIT:
            self.compact()

    def put(self, rec, slot=None):
        """Store rec and return its slot; the caller must have checked fits().

        `slot` names an existing slot whose record is replaced (rewritten in
        place when it is long enough, otherwise reclaimed and rewritten into the
        free area), or is None to use the lowest free slot / append a new one.
        """
        if slot is not None:
            off, length = self.slot(slot)
            if length >= len(rec):
                self.raw[off:off + len(rec)] = rec
                self.set_slot(slot, off, len(rec))
                return slot
            if length:
                self.set_slot(slot, 0, 0)  # the old version is superseded
        else:
            slot = self.free_slot()
        append = slot is None
        if append:
            slot = self.nslots
        # Growing the directory moves the record area's hard floor: a new slot
        # costs 4 bytes that the record must not overlap.  Compact first when
        # only a hole could provide the room.
        floor = HDR + SLACK * (self.nslots + (1 if append else 0))
        if struct.unpack_from(">H", self.raw, 4)[0] - len(rec) < floor:
            self.compact()
        if append:
            struct.pack_into(">H", self.raw, 2, slot + 1)
        end = struct.unpack_from(">H", self.raw, 4)[0] - len(rec)
        assert end >= floor, (
            "no room for record", slot, len(rec), self.nslots, self.live_lengths())
        self.raw[end:end + len(rec)] = rec
        struct.pack_into(">H", self.raw, 4, end)
        self.set_slot(slot, end, len(rec))
        return slot

    def rewrite(self, slot, rec):
        """Store rec in an existing slot, compacting holes when needed.

        Returns False if the page cannot host the record while still honouring
        its forwarding reserve, in which case the caller must move the row to
        another page and turn this slot into an `F` pointer (which always fits,
        by the reserve invariant).
        """
        if not self.fits(len(rec), reuse=slot):
            return False
        self.put(rec, slot)
        self.maybe_compact()
        return True

    def remove(self, slot):
        self.set_slot(slot, 0, 0)
        self.maybe_compact()
