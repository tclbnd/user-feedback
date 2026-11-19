"""Slow query log used by the on-call dashboard."""


class SlowLog:
    def __init__(self, threshold=50_000):
        self.threshold = threshold
        self.entries = []

    def record(self, statement, result, step):
        if result["cost"] > self.threshold:
            self.entries.append({"step": step, "statement": statement, "plan": result["plan"], "cost": result["cost"]})

    def worst(self, n=3):
        return sorted(self.entries, key=lambda e: -e["cost"])[:n]
