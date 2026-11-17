"""Heap file and secondary index files of one table, with the normal write path."""
import bisect
import json
import os
import struct

PAGE, SLOTS, SLOT, HEADER = 512, 8, 63, 8
FREE, LIVE, DEAD = 0, 1, 2
ENTRY, KEY = 40, 33


class Table:
    def __init__(self, directory):
        self.dir = directory
        with open(os.path.join(directory, "catalog.json"), encoding="utf-8") as fh:
            catalog = json.load(fh)
        self.columns, self.indexes = catalog["columns"], catalog["indexes"]
        with open(self.path("heap.db"), "rb") as fh:
            data = fh.read()
        self.pages = [bytearray(data[i:i + PAGE]) for i in range(0, len(data), PAGE)]
        self.entries = {}
        for ix in self.indexes:
            with open(self.path(f"idx_{ix['name']}.db"), "rb") as fh:
                raw = fh.read()
            count = struct.unpack(">I", raw[2:6])[0]
            items = []
            for i in range(count):
                e = raw[16 + i * ENTRY:16 + (i + 1) * ENTRY]
                items.append((e[1:1 + e[0]].decode(), struct.unpack(">IH", e[1 + KEY:ENTRY])))
            self.entries[ix["name"]] = sorted(items)

    def path(self, name):
        return os.path.join(self.dir, name)

    def slot(self, rid):
        page, slot = rid
        if page >= len(self.pages) or slot >= SLOTS:
            return None
        off = HEADER + slot * SLOT
        raw = self.pages[page]
        return raw[off], bytes(raw[off + 2:off + 2 + raw[off + 1]])

    def row(self, rid):
        got = self.slot(rid)
        if got is None or got[0] == FREE:
            return None
        return json.loads(got[1])

    def rows(self):
        for page in range(len(self.pages)):
            for slot in range(SLOTS):
                if self.slot((page, slot))[0] == LIVE:
                    yield (page, slot), self.row((page, slot))

    def value(self, row, ix):
        return row[self.columns.index(ix["column"])]

    def write_slot(self, rid, flag, data=None):
        raw, off = self.pages[rid[0]], HEADER + rid[1] * SLOT
        raw[off] = flag
        if data is not None:
            raw[off + 1] = len(data)
            raw[off + 2:off + SLOT] = data.ljust(SLOT - 2, b"\0")

    def add_entry(self, name, key, rid):
        items, item = self.entries[name], (key, tuple(rid))
        if item not in items:
            bisect.insort(items, item)

    def drop_entry(self, name, key, rid):
        items, item = self.entries[name], (key, tuple(rid))
        if item in items:
            items.remove(item)

    def insert(self, row):
        free = [(p, s) for p in range(len(self.pages)) for s in range(SLOTS) if self.slot((p, s))[0] != LIVE]
        if not free:
            self.pages.append(bytearray(b"HP" + struct.pack(">H", len(self.pages)) + b"\0" * (PAGE - 4)))
            free = [(len(self.pages) - 1, 0)]
        rid = free[0]
        self.write_slot(rid, LIVE, json.dumps(row, separators=(",", ":")).encode())
        for ix in self.indexes:
            self.add_entry(ix["name"], self.value(row, ix), rid)
        return rid

    def delete(self, rid):
        row = self.row(rid)
        if row is None:
            return
        self.write_slot(rid, DEAD)
        for ix in self.indexes:
            self.drop_entry(ix["name"], self.value(row, ix), rid)

    def update(self, rid, row):
        self.delete(rid)
        self.write_slot(rid, LIVE, json.dumps(row, separators=(",", ":")).encode())
        for ix in self.indexes:
            self.add_entry(ix["name"], self.value(row, ix), tuple(rid))

    def save(self):
        with open(self.path("heap.db"), "wb") as fh:
            fh.write(b"".join(self.pages))
        for name, items in self.entries.items():
            body = b"".join(bytes([len(k.encode())]) + k.encode().ljust(KEY, b"\0") + struct.pack(">IH", *rid)
                            for k, rid in items)
            with open(self.path(f"idx_{name}.db"), "wb") as fh:
                fh.write(b"IX" + struct.pack(">I", len(items)) + b"\0" * 10 + body)


def create(directory, columns, indexes, rows=()):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump({"columns": columns, "indexes": indexes}, fh, indent=1)
    open(os.path.join(directory, "heap.db"), "wb").close()
    for ix in indexes:
        with open(os.path.join(directory, f"idx_{ix['name']}.db"), "wb") as fh:
            fh.write(b"IX" + b"\0" * 14)
    table = Table(directory)
    for row in rows:
        table.insert(row)
    table.save()
    return table
