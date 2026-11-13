"""Resumable site-mirror crawler over a synthetic site graph.

Usage: python3 mirror.py GRAPH STATE [--workers N] [--limit N]

The graph JSON declares: origin (scheme://host, the only authorized
source), seeds, pages (keyed by normalized path+query), disallow
(path prefixes) and spacing_ms.  There is no network: every request is
replayed from the graph.  STATE persists the frontier, virtual clock,
retry state and the accumulated index, so a run interrupted by --limit
(or a crash) can be resumed by re-running with the same STATE path.

Output keys: resources, aliases, edges, skipped, requests, done.
Internal resume data is stored under "_resume".
"""
import argparse
import hashlib
import json
import re
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

KEEP_PARAMS = {"id"}  # query-parameter whitelist declared by the fixture


def remove_dot_segments(path):
    """RFC 3986 5.2.4 dot-segment removal (path must be absolute)."""
    out = []
    for seg in path.split("/"):
        if seg == ".":
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    result = "/".join(out)
    if path.endswith(("/.", "/..")):
        result += "/"
    return result


def normalize(url, base):
    """Resolve `url` against `base` and normalize.

    Strips the fragment, collapses consecutive slashes and dot
    segments, keeps only whitelisted query parameters sorted by key
    and value, and lowercases scheme/host.
    """
    sp = urlsplit(urljoin(base, url))
    scheme = sp.scheme.lower()
    netloc = sp.netloc.lower()
    path = re.sub(r"/{2,}", "/", sp.path or "/")
    path = remove_dot_segments(path)
    if not path.startswith("/"):
        path = "/" + path
    pairs = sorted(
        (k, v) for k, v in parse_qsl(sp.query, keep_blank_values=True)
        if k in KEEP_PARAMS
    )
    query = "&".join("%s=%s" % (k, v) for k, v in pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


class Crawler:
    def __init__(self, graph, workers):
        o = urlsplit(graph["origin"])
        self.origin = urlunsplit(
            (o.scheme.lower(), o.netloc.lower(), "", "", ""))
        self.base = self.origin + "/"
        self.pages = graph["pages"]
        self.disallow = list(graph.get("disallow", []))
        self.spacing = int(graph.get("spacing_ms", 0))
        self.step = max(1, self.spacing)  # virtual time strictly grows
        self.workers = max(1, int(workers))
        # --- resumable state ---
        self.frontier = deque()   # ready, deduped URLs (FIFO = fair)
        self.waiting = []         # [not_before, url] after a 429
        self.seen = set()         # every URL ever enqueued
        self.attempts = {}        # url -> number of requests made
        self.clock = 0            # virtual milliseconds
        self.resources = {}       # terminal url -> sha256(body)
        self.aliases = {}         # source url -> final url
        self.edges = set()        # (src, dst) from read bodies
        self.skipped = []         # dicts with url/reason[/target]
        self._skip_seen = set()
        self.requests = []        # dicts with url/t/status
        self.done = False

    # -- classification helpers --------------------------------------
    def same_origin(self, url):
        sp = urlsplit(url)
        return (sp.scheme.lower(), sp.netloc.lower()) == tuple(
            urlsplit(self.origin)[:2])

    def disallowed(self, url):
        path = urlsplit(url).path
        return any(path.startswith(p) for p in self.disallow)

    def skip(self, url, reason, **extra):
        key = (url, reason)
        if key in self._skip_seen:
            return
        self._skip_seen.add(key)
        entry = {"url": url, "reason": reason}
        entry.update(extra)
        self.skipped.append(entry)

    def enqueue(self, url):
        if url not in self.seen:
            self.seen.add(url)
            self.frontier.append(url)

    @staticmethod
    def key_of(abs_url):
        """Page-key form (path+query) of a normalized absolute URL."""
        sp = urlsplit(abs_url)
        return urlunsplit(("", "", sp.path, sp.query, ""))

    def seed(self, raw):
        abs_url = normalize(raw, self.base)
        if not self.same_origin(abs_url):
            self.skip(abs_url, "external")
            return
        url = self.key_of(abs_url)
        if self.disallowed(url):
            self.skip(url, "disallowed")
        elif url not in self.pages:
            self.skip(url, "missing")
        else:
            self.enqueue(url)

    # -- pure resolution of redirect/canonical chains -----------------
    def resolve(self, start):
        """Follow redirect/canonical hops from `start` (a pure function
        of the graph).  Returns an outcome dict."""
        chain, pos, cur = [], {}, start
        while True:
            if cur in pos:  # cycle detected
                cyc = chain[pos[cur]:]
                if any("redirect" in self.pages[n] for n in cyc):
                    return {"kind": "redirect_cycle", "chain": chain,
                            "cycle": cyc}
                return {"kind": "canonical_cycle", "chain": chain,
                        "cycle": cyc, "prefix": chain[:pos[cur]]}
            page = self.pages.get(cur)
            if page is None:
                return {"kind": "missing", "chain": chain, "target": cur}
            pos[cur] = len(chain)
            chain.append(cur)
            if "redirect" in page:  # redirect replaces the target
                abs_tgt = normalize(page["redirect"], self.origin + cur)
                if not self.same_origin(abs_tgt):
                    return {"kind": "external", "chain": chain,
                            "target": abs_tgt}
                tgt = self.key_of(abs_tgt)
                if self.disallowed(tgt):
                    return {"kind": "disallowed", "chain": chain,
                            "target": tgt}
                if tgt not in self.pages:
                    return {"kind": "missing", "chain": chain,
                            "target": tgt}
                cur = tgt
                continue
            if "canonical" in page:
                abs_tgt = normalize(page["canonical"], self.origin + cur)
                tgt = self.key_of(abs_tgt)
                if (not self.same_origin(abs_tgt) or self.disallowed(tgt)
                        or tgt not in self.pages):
                    # invalid canonical: ignored, page stands alone
                    bad = tgt if self.same_origin(abs_tgt) else abs_tgt
                    return {"kind": "terminal", "chain": chain,
                            "terminal": cur, "invalid_canonical": bad}
                cur = tgt
                continue
            return {"kind": "terminal", "chain": chain, "terminal": cur}

    # -- index updates -------------------------------------------------
    def add_resource(self, url):
        """Hash the terminal body; returns True if newly added."""
        if url in self.resources:
            return False
        body = self.pages[url].get("body", "")
        self.resources[url] = hashlib.sha256(
            body.encode("utf-8")).hexdigest()
        self.extract_links(url)
        return True

    def extract_links(self, src):
        for raw in self.pages[src].get("links", []):
            abs_tgt = normalize(raw, self.origin + src)
            if not self.same_origin(abs_tgt):
                self.skip(abs_tgt, "external")
                continue
            tgt = self.key_of(abs_tgt)
            if self.disallowed(tgt):
                self.skip(tgt, "disallowed")
            elif tgt not in self.pages:
                self.skip(tgt, "missing")
            else:
                self.edges.add((src, tgt))
                self.enqueue(tgt)

    # -- one request (replay) ------------------------------------------
    def process(self, url):
        page = self.pages[url]
        retry_after = page.get("retry_after_ms")
        if retry_after and self.attempts.get(url, 0) == 0:
            # first visit: throttled, must wait retry_after_ms
            self.clock += self.step
            self.attempts[url] = 1
            self.requests.append(
                {"url": url, "t": self.clock, "status": 429})
            self.waiting.append([self.clock + retry_after, url])
            return
        self.clock += self.step
        self.attempts[url] = self.attempts.get(url, 0) + 1
        self.requests.append({"url": url, "t": self.clock, "status": 200})
        res = self.resolve(url)
        kind, chain = res["kind"], res["chain"]
        if kind == "terminal":
            terminal = res["terminal"]
            for src in chain[:-1]:
                self.aliases[src] = terminal
            if "invalid_canonical" in res:
                self.skip(terminal, "invalid_canonical",
                          target=res["invalid_canonical"])
            self.add_resource(terminal)
        elif kind == "canonical_cycle":
            # stop alias resolution inside the cycle; members stand alone
            entry = res["cycle"][0]
            for src in res["prefix"]:
                self.aliases[src] = entry
            for member in res["cycle"]:
                self.skip(member, "canonical_cycle")
                self.add_resource(member)
        elif kind == "redirect_cycle":
            for node in chain:
                self.skip(node, "redirect_cycle")
        else:  # external / disallowed / missing resolution target
            self.skip(res["target"], kind)
            for src in chain:
                self.skip(src, "unresolved")

    # -- scheduling ------------------------------------------------------
    def promote(self):
        """Move waiting URLs whose Retry-After elapsed to the frontier."""
        ready_at = self.clock + self.step
        still = []
        for not_before, url in self.waiting:
            if not_before <= ready_at:
                self.frontier.append(url)
            else:
                still.append([not_before, url])
        self.waiting = still

    def run(self, limit=None):
        made = 0
        while True:
            self.promote()
            if not self.frontier:
                if not self.waiting:
                    break
                # jump the virtual clock to the next Retry-After release
                self.clock = max(
                    self.clock,
                    min(w[0] for w in self.waiting) - self.step)
                self.promote()
                if not self.frontier:
                    break
            if limit is not None and made >= limit:
                break
            batch = []
            while (self.frontier and len(batch) < self.workers
                   and (limit is None or made + len(batch) < limit)):
                batch.append(self.frontier.popleft())
            for url in batch:  # one concurrent wave, <= workers in flight
                self.process(url)
                made += 1
        self.done = not self.frontier and not self.waiting
        return made

    # -- persistence ------------------------------------------------------
    def output(self):
        return {
            "resources": [{"url": u, "sha256": h}
                          for u, h in sorted(self.resources.items())],
            "aliases": dict(sorted(self.aliases.items())),
            "edges": sorted([list(e) for e in self.edges]),
            "skipped": sorted(self.skipped, key=lambda s: (
                s["url"], s["reason"], s.get("target", ""))),
            "requests": self.requests,
            "done": self.done,
        }

    def save(self, path):
        state = self.output()
        state["_resume"] = {
            "frontier": list(self.frontier),
            "waiting": self.waiting,
            "seen": sorted(self.seen),
            "attempts": self.attempts,
            "clock": self.clock,
        }
        path.write_text(json.dumps(state, sort_keys=True) + "\n")

    @classmethod
    def load(cls, graph, workers, path):
        crawler = cls(graph, workers)
        if path.exists():
            state = json.loads(path.read_text())
            resume = state.get("_resume")
            if resume is not None:
                crawler.frontier = deque(resume["frontier"])
                crawler.waiting = [list(w) for w in resume["waiting"]]
                crawler.seen = set(resume["seen"])
                crawler.attempts = dict(resume["attempts"])
                crawler.clock = resume["clock"]
                crawler.resources = {
                    r["url"]: r["sha256"] for r in state["resources"]}
                crawler.aliases = dict(state["aliases"])
                crawler.edges = {tuple(e) for e in state["edges"]}
                crawler.skipped = list(state["skipped"])
                crawler._skip_seen = {
                    (s["url"], s["reason"]) for s in crawler.skipped}
                crawler.requests = list(state["requests"])
                crawler.done = state["done"]
                return crawler
        for raw in graph["seeds"]:
            crawler.seed(raw)
        return crawler


def main():
    p = argparse.ArgumentParser()
    p.add_argument("graph", type=Path)
    p.add_argument("state", type=Path)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    graph = json.loads(a.graph.read_text())
    crawler = Crawler.load(graph, a.workers, a.state)
    crawler.run(limit=a.limit)
    crawler.save(a.state)
    print(json.dumps(crawler.output(), sort_keys=True))


if __name__ == "__main__":
    main()
