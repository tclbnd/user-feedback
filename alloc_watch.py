"""Steady-state allocation watcher built on the garbage collector.

CPython forbids monkeypatching built-in container constructors, so instead we
snapshot every tracked container object (list, dict, set, tuple and their
subclasses) on entry and count the *new* tracked containers still alive on
exit.  Scalars created by arithmetic (int/float/complex) are not GC tracked,
so a hot path that only performs arithmetic on preallocated arrays reports
zero events, while any hidden ``[]`` / ``{}`` / tuple packing is caught.
"""

import gc

TRACKED_TYPES = (list, dict, set, tuple)


class AllocWatcher:
    def __init__(self):
        self.events = 0
        self.examples = []
        self._snapshot = None

    def _container_ids(self):
        ids = set()
        for obj in gc.get_objects():
            if isinstance(obj, TRACKED_TYPES):
                ids.add(id(obj))
        return ids

    def __enter__(self):
        gc.collect()
        self._snapshot = self._container_ids()
        return self

    def __exit__(self, *exc):
        gc.collect()
        before = self._snapshot
        for obj in gc.get_objects():
            if isinstance(obj, TRACKED_TYPES) and id(obj) not in before:
                self.events += 1
                if len(self.examples) < 5:
                    self.examples.append((type(obj).__name__,
                                          repr(obj)[:60]))
        return False
