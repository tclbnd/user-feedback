"""Slotted data page."""
import struct

SIZE, HDR = 256, 6


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
        off, length = self.slot(i)
        return bytes(self.raw[off:off + length]) if length else None

    def free(self):
        used = sum(self.slot(i)[1] for i in range(self.nslots))
        return SIZE - HDR - 4 * self.nslots - used

    def contiguous(self):
        return struct.unpack_from(">H", self.raw, 4)[0] - HDR - 4 * self.nslots

    def fits(self, size, reuse=None):
        if reuse is not None and self.slot(reuse)[1] >= size:
            return True
        return self.contiguous() >= size + (0 if reuse is not None else 4)

    def compact(self):
        recs = [rec for rec in (self.record(i) for i in range(self.nslots)) if rec]
        struct.pack_into(">H", self.raw, 2, len(recs))
        end = SIZE
        for i, rec in enumerate(recs):
            end -= len(rec)
            self.raw[end:end + len(rec)] = rec
            self.set_slot(i, end, len(rec))
        struct.pack_into(">H", self.raw, 4, end)

    def put(self, rec, slot=None):
        """Store rec in `slot` (rewrite) or in a free/new slot; caller checked fits()."""
        if slot is not None and self.slot(slot)[1] >= len(rec):
            off = self.slot(slot)[0]
            self.raw[off:off + len(rec)] = rec
            self.set_slot(slot, off, len(rec))
            return slot
        if slot is None:
            slot = self.nslots
            struct.pack_into(">H", self.raw, 2, slot + 1)
        end = struct.unpack_from(">H", self.raw, 4)[0] - len(rec)
        self.raw[end:end + len(rec)] = rec
        struct.pack_into(">H", self.raw, 4, end)
        self.set_slot(slot, end, len(rec))
        return slot

    def remove(self, slot):
        self.set_slot(slot, 0, 0)
        if self.free() - self.contiguous() > SIZE // 2:
            self.compact()
