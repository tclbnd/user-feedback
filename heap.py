"""Heap file: row ids, forwarding and overflow chains."""
import os
import struct

from page import SIZE, Page

INLINE_MAX, CHUNK, NONE = 120, SIZE - 5, 0xFFFF


class Heap:
    def __init__(self, directory):
        self.path = os.path.join(directory, "heap.db")
        os.makedirs(directory, exist_ok=True)
        data = b""
        if os.path.exists(self.path):
            with open(self.path, "rb") as fh:
                data = fh.read()
        self.pages = [bytearray(data[i:i + SIZE]) for i in range(0, len(data), SIZE)]

    def save(self):
        with open(self.path, "wb") as fh:
            fh.write(b"".join(self.pages))

    def data_page(self, pid):
        return Page(self.pages[pid])

    def store(self, pid, page):
        self.pages[pid] = page.raw

    def _make(self, data, tag):
        """Build a record for `data`: inline when short, `L` + overflow chain when long."""
        if len(data) > INLINE_MAX:
            return b"L" + struct.pack(">HI", self.store_overflow(data), len(data))
        return tag + data

    def decode(self, rec):
        if rec[:1] in (b"T", b"M"):
            return rec[1:].decode()
        first, n = struct.unpack(">HI", rec[1:7])
        out, pid = b"", first
        while pid != NONE:
            raw = self.pages[pid]
            nxt, used = struct.unpack_from(">HH", raw, 1)
            out += bytes(raw[5:5 + used])
            pid = nxt
        return out[:n].decode()

    def _alloc_overflow(self):
        """A free overflow page (empty, unlinked), else a fresh one."""
        for pid, raw in enumerate(self.pages):
            if raw[:1] == b"O" and raw[1:3] == b"\xff\xff" and raw[3:5] == b"\0\0":
                return pid
        self.pages.append(bytearray(b"O" + b"\xff\xff" + b"\0\0" + bytes(SIZE - 5)))
        return len(self.pages) - 1

    def store_overflow(self, data):
        """Write `data` to a chain of overflow pages and return the first page."""
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
        first = prev = None
        for chunk in chunks:
            pid = self._alloc_overflow()
            raw = self.pages[pid]
            raw[0:1] = b"O"
            struct.pack_into(">H", raw, 1, NONE)
            struct.pack_into(">H", raw, 3, len(chunk))
            raw[5:5 + len(chunk)] = chunk
            if prev is not None:
                struct.pack_into(">H", self.pages[prev], 1, pid)
            else:
                first = pid
            prev = pid
        return first

    def free_overflow(self, first):
        pid = first
        while pid != NONE:
            raw = self.pages[pid]
            nxt = struct.unpack_from(">H", raw, 1)[0]
            raw[0:1] = b"O"
            struct.pack_into(">H", raw, 1, NONE)
            struct.pack_into(">H", raw, 3, 0)
            pid = nxt

    def _free_record_overflow(self, rec):
        if rec is not None and rec[:1] == b"L":
            self.free_overflow(struct.unpack(">H", rec[1:3])[0])

    def place(self, rec, avoid=()):
        """Lowest data page with room (compacting if needed), else a new page."""
        for pid, raw in enumerate(self.pages):
            if raw[:1] != b"D" or pid in avoid:
                continue
            page = Page(raw)
            if not page.fits(len(rec), reuse=page.free_slot()):
                continue
            slot = page.put(rec)
            self.store(pid, page)
            return [pid, slot]
        page = Page()
        slot = page.put(rec)
        self.pages.append(page.raw)
        return [len(self.pages) - 1, slot]

    def insert(self, value):
        return self.place(self._make(value.encode(), b"T"))

    def read(self, rid):
        rec = self.data_page(rid[0]).record(rid[1])
        if rec is None or rec[:1] == b"M":
            raise KeyError(f"no row at {rid}")
        if rec[:1] == b"F":
            tp, ts = struct.unpack(">HH", rec[1:5])
            rec = self.data_page(tp).record(ts)
            if rec is None or rec[:1] == b"F":
                raise KeyError(f"no row at {rid}")
        return self.decode(rec)

    def _forward(self, page, slot, target):
        """Replace `slot` with a pointer to `target`.

        Always possible: the page keeps a forwarding reserve (see Page.reserve),
        so an `F` record fits whatever the previous record length was.
        """
        if not page.rewrite(slot, b"F" + struct.pack(">HH", *target)):
            raise AssertionError("forwarding reserve violated")

    def update(self, rid, value):
        page = self.data_page(rid[0])
        rec = page.record(rid[1])
        if rec is None or rec[:1] == b"M":
            raise KeyError(f"no row at {rid}")
        data = value.encode()
        if rec[:1] == b"F":
            # The row lives on another page. Try to grow it there; otherwise move
            # it again and repoint the *original* slot, so a row is never more
            # than one hop from its row id.
            tp, ts = struct.unpack(">HH", rec[1:5])
            target = self.data_page(tp)
            old = target.record(ts)
            new = self._make(data, b"M")
            if target.rewrite(ts, new):
                self.store(tp, target)
                self._free_record_overflow(old)
                return
            moved = self.place(new, avoid=(rid[0], tp))
            target.remove(ts)
            self.store(tp, target)
            self._free_record_overflow(old)
            self._forward(page, rid[1], moved)
            self.store(rid[0], page)
            return
        new = self._make(data, b"T")
        if page.rewrite(rid[1], new):
            self.store(rid[0], page)
            self._free_record_overflow(rec)
            return
        # Grow does not fit: migrate the record and leave a forward pointer.
        moved_rec = b"M" + new[1:] if new[:1] == b"T" else new
        moved = self.place(moved_rec, avoid=(rid[0],))
        self._forward(page, rid[1], moved)
        self.store(rid[0], page)
        self._free_record_overflow(rec)

    def delete(self, rid):
        page = self.data_page(rid[0])
        rec = page.record(rid[1])
        if rec is None or rec[:1] == b"M":
            raise KeyError(f"no row at {rid}")
        if rec[:1] == b"F":
            tp, ts = struct.unpack(">HH", rec[1:5])
            target = self.data_page(tp)
            old = target.record(ts)
            target.remove(ts)
            self.store(tp, target)
            self._free_record_overflow(old)
        else:
            self._free_record_overflow(rec)
        page.remove(rid[1])
        self.store(rid[0], page)

    def stats(self):
        out = []
        for pid, raw in enumerate(self.pages):
            if raw[:1] == b"D":
                p = Page(raw)
                out.append({"page": pid, "kind": "data", "slots": p.nslots, "free": p.free()})
            else:
                out.append({"page": pid, "kind": "overflow", "slots": 0, "free": 0})
        return out
