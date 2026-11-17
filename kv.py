"""Committed key/value state."""


class Store:
    def __init__(self):
        self.data = {}

    def snapshot(self):
        return dict(self.data)

    def install(self, writes):
        self.data.update(writes)

    @staticmethod
    def scan(view, lo, hi):
        return sorted((k, v) for k, v in view.items() if lo <= k < hi)
