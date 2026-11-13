"""Deterministic local site-graph crawler with a resumable virtual-clock frontier.

The crawler replays a synthetic site graph (no network):

* same-origin enforcement and URL normalization (dot segments, collapsed
  repeated slashes, fragment removal, whitelisted query parameters);
* relative link / redirect / canonical resolution with provenance aliases,
  cycle detection and invalid-canonical handling;
* a resumable concurrent frontier with per-host concurrency and rate limits,
  robots-style disallow rules and Retry-After honoring, driven by a strictly
  monotonic virtual clock;
* an index of resources (sha256), edges, skip reasons and a replayable
  request/event log.

State is checkpointed after every scheduling step so that a crash (--limit or
an external kill) never loses a discovered, compliant URL.
"""
import argparse
import hashlib
import json
from collections import deque
from pathlib import Path

WHITELIST_QUERY = ("id",)  # only these query parameters are meaningful


# --------------------------------------------------------------------------- #
# URL normalization / origin handling
# --------------------------------------------------------------------------- #
def collapse_path(path):
    """Collapse repeated slashes and resolve '.' / '..' dot segments."""
    is_dir = path.endswith("/")
    stack = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if stack:
                stack.pop()
            continue
        stack.append(seg)
    out = "/" + "/".join(stack)
    if is_dir and not out.endswith("/"):
        out += "/"
    return out or "/"


def origin_key(origin):
    """Return (scheme, hostname, port) for an origin with an explicit scheme."""
    scheme, rest = origin.split("://", 1)
    rest = rest.split("/", 1)[0]
    hostname, _, port = rest.partition(":")
    return scheme.lower(), hostname, port


def host_of(url):
    return url.split("://", 1)[1].split("/", 1)[0]


def normalize_url(raw, origin, base=None):
    """Resolve *raw* against *origin* (and optional *base* URL), normalize it.

    Drops fragments, collapses repeated slashes and dot segments, and keeps
    only whitelisted query parameters sorted by (key, value). Returns an
    absolute same-origin URL. Raises ValueError for external or empty refs.
    """
    if raw is None:
        raise ValueError("empty url")
    s = str(raw).strip()
    if not s:
        raise ValueError("empty url")
    s = s.split("#", 1)[0]
    if not s:
        raise ValueError("fragment-only url")

    scheme, host, port = origin_key(origin)
    origin_base = scheme + "://" + host + ((":" + port) if port else "")

    if "://" in s:  # absolute URL
        ref_scheme, rest = s.split("://", 1)
        if "/" in rest:
            ref_host, path = rest.split("/", 1)
            path = "/" + path
        else:
            ref_host, path = rest, "/"
        ref_hostname, _, ref_port = ref_host.partition(":")
        if (ref_scheme.lower(), ref_hostname, ref_port) != (scheme, host, port):
            raise ValueError("external origin: " + s)
    elif s.startswith("//"):  # scheme-relative URL
        rest = s[2:]
        if "/" in rest:
            ref_host, path = rest.split("/", 1)
            path = "/" + path
        else:
            ref_host, path = rest, "/"
        ref_hostname, _, ref_port = ref_host.partition(":")
        if (ref_hostname, ref_port) != (host, port):
            raise ValueError("external origin: " + s)
    elif s.startswith("/"):  # origin-absolute path
        path = s
    else:  # relative reference: resolve against the base URL's directory
        if base is None:
            base_path = "/"
        else:
            base_path = "/" + base[len(origin_base):].split("?", 1)[0].lstrip("/")
        base_dir = base_path.rsplit("/", 1)[0] + "/"
        path = base_dir + s

    if "?" in path:
        path, query = path.split("?", 1)
    else:
        query = ""
    if not path:
        path = "/"

    pairs = []
    if query:
        for kv in query.split("&"):
            if not kv:
                continue
            key, _, val = kv.partition("=")
            if key in WHITELIST_QUERY:
                pairs.append((key, val))
    pairs.sort()
    qs = "&".join(k + (("=" + v) if v != "" else "") for k, v in pairs)
    out = origin_base + collapse_path(path)
    if qs:
        out += "?" + qs
    return out


def page_key(url, origin):
    """Graph page-table key: path plus query string, rooted at '/'."""
    prefix = normalize_url("/", origin).rstrip("/")  # scheme://host[:port]
    key = url[len(prefix):]
    return key if key.startswith("/") else "/" + key


def is_disallowed(path, disallow_prefixes):
    return any(path.startswith(p) for p in disallow_prefixes)


# --------------------------------------------------------------------------- #
# Crawler
# --------------------------------------------------------------------------- #
class Crawler:
    def __init__(self, graph, workers):
        self.origin = graph["origin"]
        origin_key(self.origin)  # validates that the origin carries a scheme
        self.spacing_ms = int(graph.get("spacing_ms", 0))
        self.workers = max(1, int(workers))
        self.disallow = list(graph.get("disallow", []))
        # normalize page-table keys up front so lookups are unambiguous
        self.pages = {}
        for k, v in graph.get("pages", {}).items():
            nk = page_key(normalize_url(k, self.origin), self.origin)
            self.pages[nk] = v

        self.clock = 0
        self.next_seq = 0
        self.host_state = {}          # host -> scheduling state
        self.discovered = {}          # url -> {"source", "seq"}
        self.requests = []            # replayable per-request event log
        self.skipped = []
        self.read_pages = {}          # url -> sha256 of every fetched 200 body
        self.links_seen = {}          # url -> sorted kept normalized targets
        self.canonical_map = {}       # url -> valid canonical target
        self.canonical_invalid = set()
        self.redirect_map = {}        # url -> requested redirect target

    # -- small helpers ----------------------------------------------------- #
    @staticmethod
    def _dir_base(url):
        """Directory URL used to resolve relative references per RFC 3986."""
        head, _, _ = url.partition("?")
        if "/" in head.split("://", 1)[1]:
            return head.rsplit("/", 1)[0] + "/"
        return url

    def _hs(self, host):
        hs = self.host_state.get(host)
        if hs is None:
            hs = {
                "queue": deque(),       # FIFO waiting URLs (deduped frontier)
                "inflight": 0,          # current in-flight request count
                "inflight_urls": [],    # urls in flight (requeue on crash)
                "next_ready": 0,        # rate-limit gate (spacing)
                "retry_after": {},      # url -> earliest allowed virtual time
            }
            self.host_state[host] = hs
        return hs

    def discover(self, raw, source=None):
        """Normalize *raw*; enqueue a compliant URL or record a skip reason."""
        base = self._dir_base(source) if source and "://" in source else None
        try:
            url = normalize_url(raw, self.origin, base=base)
        except ValueError:
            self.skipped.append({"url": str(raw), "reason": "external",
                                 "source": source})
            return None
        key = page_key(url, self.origin)
        if is_disallowed(key, self.disallow):
            rule = next(p for p in self.disallow if key.startswith(p))
            self.skipped.append({"url": url, "reason": "disallow",
                                 "source": source, "rule": rule})
            return None
        if url in self.discovered:
            return url
        self.discovered[url] = {"source": source, "seq": self.next_seq}
        self.next_seq += 1
        hs = self._hs(host_of(url))
        hs["queue"].append(url)
        return url

    # -- virtual-clock scheduler ------------------------------------------ #
    def _dispatchable(self, hs):
        if not hs["queue"] or hs["inflight"] >= self.workers:
            return False
        if self.clock < hs["next_ready"]:
            return False
        gate = hs["retry_after"].get(hs["queue"][0])
        return gate is None or self.clock >= gate

    def _next_wake(self):
        candidates = []
        for hs in self.host_state.values():
            if not hs["queue"]:
                continue
            t = hs["next_ready"]
            gate = hs["retry_after"].get(hs["queue"][0])
            if gate is not None:
                t = max(t, gate)
            candidates.append(t)
        return min(candidates) if candidates else None

    def _dispatch(self, hs, url):
        hs["queue"].popleft()
        hs["inflight"] += 1
        hs["inflight_urls"].append(url)
        t = self.clock
        key = page_key(url, self.origin)
        page = self.pages.get(key)
        throttled = (page is not None
                     and page.get("retry_after_ms") is not None
                     and url not in hs["retry_after"])
        if page is None:
            status = 404
        elif "redirect" in page:
            status = 302
        elif throttled:
            status = 429
            hs["retry_after"][url] = t + int(page["retry_after_ms"])
        else:
            status = 200
            hs["retry_after"].pop(url, None)  # Retry-After satisfied
        self.requests.append({"url": url, "t": t, "status": status})
        # virtual fetches complete at the same tick (graph replay has no I/O);
        # next request on this host still respects the spacing gate
        hs["next_ready"] = t + self.spacing_ms
        return status, page

    def _complete(self, hs, url, status, page):
        hs["inflight"] -= 1
        hs["inflight_urls"].remove(url)
        if status == 429:
            hs["queue"].appendleft(url)   # blocked until Retry-After gate
            return
        if status == 404:
            self.skipped.append({"url": url, "reason": "missing"})
            return
        if status == 302:
            try:
                target = normalize_url(page["redirect"], self.origin,
                                       base=self._dir_base(url))
            except ValueError:
                self.skipped.append({"url": url, "reason": "invalid_redirect",
                                     "target": str(page["redirect"])})
                return
            if is_disallowed(page_key(target, self.origin), self.disallow):
                self.skipped.append({"url": url,
                                     "reason": "redirect_disallowed",
                                     "target": target})
                return
            self.redirect_map[url] = target
            self.discover(target, source=url)
            return
        # status 200: read body, links and canonical
        body = page.get("body", "")
        self.read_pages[url] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        kept = set()
        for raw in page.get("links", []):
            target = self.discover(raw, source=url)
            if target is not None:
                kept.add(target)
        self.links_seen[url] = kept
        if "canonical" in page:
            self._handle_canonical(url, page["canonical"])

    def _handle_canonical(self, url, raw):
        try:
            target = normalize_url(raw, self.origin, base=self._dir_base(url))
        except ValueError:
            self.canonical_invalid.add(url)
            self.skipped.append({"url": url, "reason": "invalid_canonical",
                                 "target": str(raw)})
            return
        if is_disallowed(page_key(target, self.origin), self.disallow):
            self.canonical_invalid.add(url)
            self.skipped.append({"url": url, "reason": "canonical_disallowed",
                                 "target": target})
            return
        if page_key(target, self.origin) not in self.pages:
            self.canonical_invalid.add(url)
            self.skipped.append({"url": url, "reason": "canonical_missing",
                                 "target": target})
            return
        self.canonical_map[url] = target
        self.discover(target, source=url)  # terminal must itself be fetched

    def run(self, limit):
        run_start = len(self.requests)
        while True:
            if limit is not None and len(self.requests) - run_start >= limit:
                return
            ready = [h for h, hs in self.host_state.items()
                     if self._dispatchable(hs)]
            if not ready:
                nxt = self._next_wake()
                if nxt is None or nxt <= self.clock:
                    return  # frontier drained
                self.clock = nxt
                continue
            # fair global choice: host whose head URL was discovered earliest
            host = min(ready, key=lambda h:
                       self.discovered[self.host_state[h]["queue"][0]]["seq"])
            hs = self.host_state[host]
            url = hs["queue"][0]
            status, page = self._dispatch(hs, url)
            self._complete(hs, url, status, page)  # instant virtual completion

    # -- finalization ------------------------------------------------------ #
    def _resolve_aliases(self):
        """Walk the combined redirect+canonical graph; detect cycles.

        Returns (aliases, cycle_nodes, dead_redirects). A redirect chain whose
        terminal page was never fetched as 200 (e.g. a 404 target) is not a
        successfully resolved alias: every member of such a chain lands in
        dead_redirects instead.
        """
        edges = dict(self.redirect_map)
        edges.update(self.canonical_map)
        aliases, cycle_nodes, dead = {}, set(), set()
        for start in edges:
            seen, path, node = {}, [], start
            while node in edges and node not in aliases and node not in seen \
                    and node not in dead:
                seen[node] = len(path)
                path.append(node)
                node = edges[node]
            if node in seen:  # ring found: freeze ring, resolve prefix to it
                ring = path[seen[node]:]
                cycle_nodes.update(ring)
                for n in path[:seen[node]]:
                    aliases[n] = ring[0]
            elif node in aliases:
                terminal = aliases[node]
                for n in reversed(path):
                    aliases[n] = terminal
            elif node in dead:
                dead.update(path)
            elif node in edges:
                # reached an already-frozen ring member
                cycle_nodes.add(node)
                for n in reversed(path):
                    aliases[n] = node
            elif node in self.read_pages:
                # terminal fetched as a real page: whole chain resolves to it
                terminal = node
                for n in reversed(path):
                    aliases[n] = terminal
            else:
                # terminal neither an alias source nor a fetched body (404):
                # the whole redirect chain is dead, not a successful alias
                dead.update(path)
        return aliases, cycle_nodes, dead

    def is_done(self):
        return all(not hs["queue"] and hs["inflight"] == 0
                   for hs in self.host_state.values())

    def finalize(self):
        aliases, cycle_nodes, dead = self._resolve_aliases()
        for url in sorted(cycle_nodes):
            kind = ("redirect_cycle" if url in self.redirect_map
                    else "canonical_cycle")
            self.skipped.append({"url": url, "reason": "cycle", "kind": kind})
        for url in sorted(dead):
            self.skipped.append({"url": url, "reason": "redirect_missing"})

        non_terminal = set(aliases) | cycle_nodes | dead
        resources = sorted(
            ({"url": u, "sha256": d}
             for u, d in self.read_pages.items() if u not in non_terminal),
            key=lambda r: r["url"],
        )
        edge_set = {(src, dst)
                    for src, targets in self.links_seen.items()
                    for dst in targets}
        return {
            "resources": resources,
            "aliases": {k: aliases[k] for k in sorted(aliases)},
            "edges": [list(e) for e in sorted(edge_set)],
            "skipped": sorted(self.skipped,
                              key=lambda s: (s["reason"], s["url"],
                                             s.get("target", ""))),
            "requests": self.requests,
            "done": True,
            "partial": False,
        }

    # -- resumable state --------------------------------------------------- #
    def to_state(self, partial):
        return {
            "partial": partial,
            "done": not partial,
            "clock": self.clock,
            "next_seq": self.next_seq,
            "discovered": self.discovered,
            "host_state": {
                h: {"queue": list(hs["queue"]),
                    "inflight": hs["inflight"],
                    "inflight_urls": list(hs["inflight_urls"]),
                    "next_ready": hs["next_ready"],
                    "retry_after": dict(hs["retry_after"])}
                for h, hs in self.host_state.items()},
            "requests": self.requests,
            "skipped": self.skipped,
            "read_pages": self.read_pages,
            "links_seen": {u: sorted(v) for u, v in self.links_seen.items()},
            "canonical_map": self.canonical_map,
            "canonical_invalid": sorted(self.canonical_invalid),
            "redirect_map": self.redirect_map,
        }

    @classmethod
    def from_state(cls, graph, workers, state):
        c = cls(graph, workers)
        c.clock = state["clock"]
        c.next_seq = state["next_seq"]
        c.discovered = state["discovered"]
        c.host_state = {}
        for h, hs in state["host_state"].items():
            queue = deque(hs["queue"])
            inflight = hs.get("inflight", 0)
            inflight_urls = list(hs.get("inflight_urls", []))
            # a crash mid-fetch: those URLs were never answered, requeue them
            for u in reversed(inflight_urls):
                queue.appendleft(u)
            c.host_state[h] = {
                "queue": queue,
                "inflight": 0 if inflight_urls else inflight,
                "inflight_urls": [],
                "next_ready": hs["next_ready"],
                "retry_after": dict(hs["retry_after"]),
            }
        c.requests = state["requests"]
        c.skipped = state["skipped"]
        c.read_pages = state["read_pages"]
        c.links_seen = {u: set(v) for u, v in state["links_seen"].items()}
        c.canonical_map = state["canonical_map"]
        c.canonical_invalid = set(state["canonical_invalid"])
        c.redirect_map = state["redirect_map"]
        return c


def build_crawler(graph, workers, state_path):
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if not state.get("partial", False):
            raise SystemExit(
                "state file %s is already complete; remove it to recrawl"
                % state_path)
        return Crawler.from_state(graph, workers, state)
    crawler = Crawler(graph, workers)
    for seed in graph["seeds"]:
        crawler.discover(seed, source="seed")
    return crawler


def main():
    p = argparse.ArgumentParser(description="Replay a synthetic site graph.")
    p.add_argument("graph", type=Path)
    p.add_argument("state", type=Path)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    if a.limit is not None and a.limit <= 0:
        p.error("--limit must be a positive integer")
    if a.workers <= 0:
        p.error("--workers must be a positive integer")

    graph = json.loads(a.graph.read_text())
    crawler = build_crawler(graph, a.workers, a.state)
    crawler.run(a.limit)

    if crawler.is_done():
        result = crawler.finalize()
    else:
        # resumable checkpoint: every discovered compliant URL, the virtual
        # clock and retry state are persisted; rerun with the same state file
        result = crawler.to_state(partial=True)
    a.state.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(
        {"done": result["done"], "partial": result.get("partial", False),
         "requests": len(result["requests"]),
         "queued": sum(len(hs["queue"])
                       for hs in crawler.host_state.values()) if not
         result["done"] else 0},
        ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
