"""Wait-for graph based deadlock detection.

Replaces the old lock-wait timeout.  The graph has an edge ``W -> H`` when
transaction ``W`` is queued for a resource where ``H`` holds an incompatible
lock, or where ``H`` is queued ahead of ``W`` with an incompatible mode
(conversions only wait for holders).  Edges are maintained incrementally as
requests block, get granted and locks are released; every time a request
blocks, a depth-first search looks for a cycle through that requester.
"""
from __future__ import annotations


class DeadlockDetector:
    def __init__(self, lock_table):
        self.locks = lock_table
        # waiter -> set of transactions it is currently waiting for
        self.graph = {}

    def add_waiter(self, tx):
        """Record edges for a transaction that just blocked."""
        self.graph[tx] = self.locks.wait_edges(tx)
        # A new queue entry may land ahead of earlier waiters, so the edges
        # of every other waiter on that resource can change as well.
        self.refresh_all()

    def remove(self, tx):
        """Drop the node when the transaction stops waiting or finishes."""
        self.graph.pop(tx, None)
        for waiters in self.graph.values():
            waiters.discard(tx)

    def refresh(self, tx):
        if tx in self.graph:
            self.graph[tx] = self.locks.wait_edges(tx)

    def refresh_all(self):
        for waiter in list(self.graph):
            if self.locks.is_waiting(waiter):
                self.graph[waiter] = self.locks.wait_edges(waiter)
            else:
                self.remove(waiter)

    def refresh_resources(self, resources):
        affected = set()
        for res in resources:
            r = self.locks.resources.get(res)
            if r is not None:
                affected.update(r.queued_tx())
        for waiter in list(self.graph):
            if waiter in affected or not self.locks.is_waiting(waiter):
                self.refresh(waiter)
        for waiter in affected:
            if self.locks.is_waiting(waiter) and waiter not in self.graph:
                self.graph[waiter] = self.locks.wait_edges(waiter)

    def find_cycle(self, root):
        """Return a cycle through `root` as a list of tx ids, else None.

        Depth-first search following smaller transaction ids first, so the
        reported cycle is deterministic.
        """
        if root not in self.graph:
            return None
        stack = []
        on_stack = set()

        def dfs(node):
            stack.append(node)
            on_stack.add(node)
            for nxt in sorted(self.graph.get(node, ())):
                if nxt == root and len(stack) >= 1:
                    return stack[:]
                if nxt in on_stack or nxt not in self.graph:
                    continue
                found = dfs(nxt)
                if found is not None:
                    return found
            stack.pop()
            on_stack.discard(node)
            return None

        return dfs(root)

    def pick_victim(self, cycle, written, started):
        """Fewest written rows; ties broken by the latest begin time."""
        def key(tx):
            return (written.get(tx, 0), -started.get(tx, 0))

        return min(cycle, key=key)
