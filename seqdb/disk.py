"""In-memory stand-in for a node's local disk.

Writes land in a page cache first and only become durable after ``sync``.
``crash`` throws away everything that was not synced, which is what a power
loss or a killed process does to buffered writes.
"""


class Disk:
    def __init__(self):
        self._durable = {}
        self._cached = {}
        self.syncs = 0

    def read(self, name):
        if name in self._cached:
            return self._cached[name]
        return self._durable.get(name, "")

    def write(self, name, data):
        self._cached[name] = data

    def append(self, name, data):
        self._cached[name] = self.read(name) + data

    def sync(self, name):
        if name in self._cached:
            self._durable[name] = self._cached.pop(name)
        self.syncs += 1

    def crash(self):
        self._cached.clear()

    def names(self):
        return sorted(set(self._durable) | set(self._cached))
