"""Committed key/value state."""


class Store:
    def __init__(self):
        self.data = {}

    def snapshot(self):
        return dict(self.data)

    def install(self, writes):
        for key, value in writes.items():
            if value is None:
                self.data.pop(key, None)
            else:
                self.data[key] = value

    @staticmethod
    def scan(view, lo, hi):
        return sorted((k, v) for k, v in view.items() if lo <= k < hi)
