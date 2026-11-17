"""Heap file: row ids, forwarding and overflow chains."""
import os
import struct

from page import SIZE, Page

INLINE_MAX, CHUNK, NONE = 120, SIZE - 5, 0xFFFF


class Heap:
    def __init__(self, directory):
        self.path = os.path.join(directory, "heap.db")
        os.makedirs(directory, exist_ok=True)
        data = open(self.path, "rb").read() if os.path.exists(self.path) else b""
        self.pages = [bytearray(data[i:i + SIZE]) for i in range(0, len(data), SIZE)]

    def save(self):
        with open(self.path, "wb") as fh:
            fh.write(b"".join(self.pages))

    def data_page(self, pid):
        return Page(self.pages[pid])

    def store(self, pid, page):
        self.pages[pid] = page.raw

    def encode(self, value, tag=b"T"):
        data = value.encode()
        if len(data) > SIZE - 11:
            raise ValueError("record too large")
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

    def place(self, rec, avoid=None):
        for pid, raw in enumerate(self.pages):
            if raw[:1] == b"D" and pid != avoid and Page(raw).fits(len(rec)):
                page = Page(raw)
                slot = page.put(rec)
                self.store(pid, page)
                return [pid, slot]
        page = Page()
        self.pages.append(page.raw)
        return [len(self.pages) - 1, page.put(rec)]

    def insert(self, value):
        return self.place(self.encode(value))

    def read(self, rid):
        rec = self.data_page(rid[0]).record(rid[1])
        if rec is None or rec[:1] == b"M":
            raise KeyError(f"no row at {rid}")
        while rec[:1] == b"F":
            tp, ts = struct.unpack(">HH", rec[1:5])
            rec = self.data_page(tp).record(ts)
        return self.decode(rec)

    def update(self, rid, value):
        page = self.data_page(rid[0])
        rec = page.record(rid[1])
        if rec[:1] == b"F":
            tp, ts = struct.unpack(">HH", rec[1:5])
            target = self.data_page(tp)
            new = self.encode(value, b"M")
            if target.fits(len(new), reuse=ts):
                target.put(new, ts)
                self.store(tp, target)
                return
            moved = self.place(new, avoid=tp)
            target.put(b"F" + struct.pack(">HH", *moved), ts)
            self.store(tp, target)
            return
        new = self.encode(value)
        if page.fits(len(new), reuse=rid[1]):
            page.put(new, rid[1])
        else:
            moved = self.place(self.encode(value, b"M"), avoid=rid[0])
            page.put(b"F" + struct.pack(">HH", *moved), rid[1])
        self.store(rid[0], page)

    def delete(self, rid):
        page = self.data_page(rid[0])
        rec = page.record(rid[1])
        if rec[:1] == b"F":
            tp, ts = struct.unpack(">HH", rec[1:5])
            target = self.data_page(tp)
            target.remove(ts)
            self.store(tp, target)
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
