"""Latency bookkeeping for the service dashboard."""


class Histogram:
    BUCKETS = (1, 2, 5, 10, 20, 50, 100)

    def __init__(self):
        self.counts = [0] * (len(self.BUCKETS) + 1)
        self.total = 0

    def observe(self, value):
        for i, edge in enumerate(self.BUCKETS):
            if value <= edge:
                self.counts[i] += 1
                break
        else:
            self.counts[-1] += 1
        self.total += 1

    def quantile(self, q):
        if not self.total:
            return 0
        target = q * self.total
        seen = 0
        for i, count in enumerate(self.counts):
            seen += count
            if seen >= target:
                return self.BUCKETS[i] if i < len(self.BUCKETS) else float("inf")
        return float("inf")
