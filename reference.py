"""Independent reference traversal used as the acceptance truth oracle.

This module deliberately re-implements normalization and traversal
independently from mirror.py (urllib.parse based, BFS over graph semantics),
so agreement between the two is meaningful rather than shared-code tautology.
It produces the expected sets of resources, edges, canonical/alias groups and
skip categories for a synthetic site graph.
"""
import hashlib
from collections import deque
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urljoin

WHITELIST = ("id",)


def ref_normalize(raw, origin, base=None):
    """Mirror the README normalization rules using urllib primitives."""
    if raw is None or not str(raw).strip():
        raise ValueError("empty")
    s = str(raw).strip().split("#", 1)[0]
    if not s:
        raise ValueError("fragment only")
    joined = urljoin(base or (origin + "/"), s)
    parts = urlsplit(joined)
    op = urlsplit(origin)
    if (parts.scheme.lower(), parts.hostname,
            parts.port or None) != (op.scheme.lower(), op.hostname,
                                    op.port or None):
        raise ValueError("external: " + s)
    path = parts.path or "/"
    # collapse repeated slashes, then resolve dot segments (os-independent)
    while "//" in path:
        path = path.replace("//", "/")
    segs, stack = path.split("/"), []
    trailing = path.endswith("/")
    for seg in segs:
        if seg in ("", "."):
            continue
        if seg == "..":
            if stack:
                stack.pop()
        else:
            stack.append(seg)
    path = "/" + "/".join(stack)
    if trailing and not path.endswith("/"):
        path += "/"
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k in WHITELIST]
    q.sort()
    query = "&".join(k + ("=" + v if v != "" else "") for k, v in q)
    host = parts.hostname + ((":%d" % parts.port) if parts.port else "")
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def ref_key(url, origin):
    p = urlsplit(url)
    return p.path + (("?" + p.query) if p.query else "")


def reference_index(graph):
    """BFS truth oracle: returns resources, aliases, edges, skip-reason sets."""
    origin = graph["origin"]
    disallow = tuple(graph.get("disallow", []))
    pages = graph["pages"]

    def blocked(key):
        return any(key.startswith(d) for d in disallow)

    queue = deque()
    seen = set()
    skipped = []

    def enqueue(raw, source):
        try:
            url = ref_normalize(raw, origin, base=source)
        except ValueError:
            skipped.append(("external", str(raw)))
            return
        key = ref_key(url, origin)
        if blocked(key):
            skipped.append(("disallow", url))
            return
        if url not in seen:
            seen.add(url)
            queue.append(url)

    for seed in graph["seeds"]:
        enqueue(seed, None)

    read = {}                 # url -> sha256
    edge_pairs = set()
    redirects = {}
    canonicals = {}
    throttled_once = set()    # pages already served their synthetic 429

    while queue:
        url = queue.popleft()
        key = ref_key(url, origin)
        page = pages.get(key)
        if page is None:
            skipped.append(("missing", url))
            continue
        # mirror the replay rule: first visit of a throttled page is a 429,
        # the second visit serves the real page
        if page.get("retry_after_ms") is not None and url not in throttled_once:
            throttled_once.add(url)
            queue.appendleft(url)
            continue
        if "redirect" in page:
            try:
                target = ref_normalize(page["redirect"], origin, base=url)
            except ValueError:
                skipped.append(("invalid_redirect", url))
                continue
            if blocked(ref_key(target, origin)):
                skipped.append(("redirect_disallowed", url))
                continue
            redirects[url] = target
            enqueue(target, url)
            continue
        body = page.get("body", "")
        read[url] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        for raw in page.get("links", []):
            try:
                target = ref_normalize(raw, origin, base=url)
            except ValueError:
                skipped.append(("external", str(raw)))
                continue
            if blocked(ref_key(target, origin)):
                skipped.append(("disallow", target))
                continue
            edge_pairs.add((url, target))
            enqueue(target, url)
        if "canonical" in page:
            try:
                target = ref_normalize(page["canonical"], origin, base=url)
            except ValueError:
                skipped.append(("invalid_canonical", url))
                continue
            tkey = ref_key(target, origin)
            if blocked(tkey):
                skipped.append(("canonical_disallowed", url))
                continue
            if tkey not in pages:
                skipped.append(("canonical_missing", url))
                continue
            canonicals[url] = target
            enqueue(target, url)

    # alias graph resolution with cycle detection
    edges = dict(redirects)
    edges.update(canonicals)
    aliases, cycles, dead = {}, set(), set()
    for start in edges:
        path, seen_local, node = [], {}, start
        while (node in edges and node not in seen_local
               and node not in aliases and node not in dead):
            seen_local[node] = len(path)
            path.append(node)
            node = edges[node]
        if node in seen_local:
            ring = path[seen_local[node]:]
            cycles.update(ring)
            for n in path[:seen_local[node]]:
                aliases[n] = ring[0]
        elif node in aliases:
            term = aliases[node]
            for n in reversed(path):
                aliases[n] = term
        elif node in dead:
            dead.update(path)
        elif node in edges:
            # reached an already-frozen ring member: prefix aliases into ring
            cycles.add(node)
            for n in reversed(path):
                aliases[n] = node
        elif node in read:
            # terminal fetched as a real page: whole chain resolves to it
            for n in reversed(path):
                aliases[n] = node
        else:
            # terminal is neither an alias source nor a fetched body (404):
            # the whole redirect chain is dead
            dead.update(path)

    non_terminal = set(aliases) | cycles | dead
    resources = sorted(
        ({"url": u, "sha256": d} for u, d in read.items()
         if u not in non_terminal),
        key=lambda r: r["url"])
    for u in sorted(cycles):
        skipped.append(("cycle", u))
    for u in sorted(dead):
        skipped.append(("redirect_missing", u))

    # canonical groups: every alias chain grouped by terminal URL
    groups = {}
    for src, term in aliases.items():
        groups.setdefault(term, set()).add(src)

    return {
        "resources": resources,
        "aliases": {k: aliases[k] for k in sorted(aliases)},
        "edges": sorted(edge_pairs),
        "skip_reasons": sorted(set(r for r, _ in skipped)),
        "skip_detail": sorted(skipped),
        "canonical_groups": {k: sorted(v) for k, v in sorted(groups.items())},
        "cycles": sorted(cycles),
        "read": read,
    }
