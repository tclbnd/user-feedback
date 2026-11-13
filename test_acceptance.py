"""Acceptance tests for mirror.py.

1. A 70-page site graph with four cycle kinds (link, redirect,
   canonical, mixed) is crawled with --workers 1 and 6; the final
   index must match an independent reference traversal (nodes, edges,
   canonical groups) implemented below from the spec.
2. A virtual-clock run with 429/Retry-After pages, a restart via
   --limit and waiting-queue recovery: every request is checked for
   zero blocked-path access, spacing and Retry-After lower bounds, and
   the final index must still equal the ground truth.
"""
import hashlib
import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

WORK = Path(__file__).resolve().parent
MIRROR = WORK / "mirror.py"
TMP = WORK / ".test_tmp"


# ---------------------------------------------------------------------
# Independent reference implementation (written directly from the spec,
# sharing no code with mirror.py).
# ---------------------------------------------------------------------

def ref_norm(url, base):
    joined = urljoin(base, url)
    sp = urlsplit(joined)
    path = sp.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    out, trailing = [], False
    for seg in path.split("/"):
        if seg == ".":
            trailing = True
            continue
        if seg == "..":
            if out:
                out.pop()
            trailing = True
            continue
        out.append(seg)
        trailing = False
    path = "/".join(out) + ("/" if trailing else "")
    if not path.startswith("/"):
        path = "/" + path
    keep = sorted(kv for kv in parse_qsl(sp.query, keep_blank_values=True)
                  if kv[0] == "id")
    query = "&".join("%s=%s" % kv for kv in keep)
    return urlunsplit((sp.scheme.lower(), sp.netloc.lower(), path,
                       query, ""))


def reference_index(graph):
    origin = graph["origin"]
    o = urlsplit(origin)
    origin_parts = (o.scheme.lower(), o.netloc.lower())
    pages = graph["pages"]
    disallow = list(graph.get("disallow", []))

    def dis(key):
        return any(key.startswith(p) for p in disallow)

    def to_key(raw, base):
        absu = ref_norm(raw, base)
        sp = urlsplit(absu)
        if (sp.scheme, sp.netloc) != origin_parts:
            return None, absu
        return urlunsplit(("", "", sp.path, sp.query, "")), absu

    def resolve(start):
        chain, cur = [], start
        while True:
            if cur in chain:
                cyc = chain[chain.index(cur):]
                if any("redirect" in pages[n] for n in cyc):
                    return ("redirect_cycle", chain, cyc, None)
                return ("canonical_cycle", chain, cyc, None)
            page = pages.get(cur)
            if page is None:
                return ("missing", chain, None, cur)
            chain.append(cur)
            if "redirect" in page:
                key, absu = to_key(page["redirect"], origin + cur)
                if key is None:
                    return ("external", chain, None, absu)
                if dis(key):
                    return ("disallowed", chain, None, key)
                if key not in pages:
                    return ("missing", chain, None, key)
                cur = key
                continue
            if "canonical" in page:
                key, absu = to_key(page["canonical"], origin + cur)
                if key is None or dis(key) or key not in pages:
                    return ("terminal", chain, None,
                            (cur, absu if key is None else key))
                cur = key
                continue
            return ("terminal", chain, None, (cur, None))

    resources, aliases, edges, skipped = {}, {}, set(), set()
    seen, queue = set(), deque()

    def add_links(src):
        for raw in pages[src].get("links", []):
            key, absu = to_key(raw, origin + src)
            if key is None:
                skipped.add((absu, "external"))
            elif dis(key):
                skipped.add((key, "disallowed"))
            elif key not in pages:
                skipped.add((key, "missing"))
            else:
                edges.add((src, key))
                if key not in seen:
                    seen.add(key)
                    queue.append(key)

    def add_res(u):
        if u not in resources:
            resources[u] = hashlib.sha256(
                pages[u].get("body", "").encode("utf-8")).hexdigest()
            add_links(u)

    def consider(raw, base):
        key, absu = to_key(raw, base)
        if key is None:
            skipped.add((absu, "external"))
        elif dis(key):
            skipped.add((key, "disallowed"))
        elif key not in pages:
            skipped.add((key, "missing"))
        elif key not in seen:
            seen.add(key)
            queue.append(key)

    for raw in graph["seeds"]:
        consider(raw, origin + "/")
    while queue:
        u = queue.popleft()
        kind, chain, cyc, extra = resolve(u)
        if kind == "terminal":
            term, bad = extra
            for s in chain[:-1]:
                aliases[s] = term
            if bad is not None:
                skipped.add((term, "invalid_canonical"))
            add_res(term)
        elif kind == "canonical_cycle":
            entry = cyc[0]
            for s in chain[:chain.index(entry)]:
                aliases[s] = entry
            for m in cyc:
                skipped.add((m, "canonical_cycle"))
                add_res(m)
        elif kind == "redirect_cycle":
            for n in chain:
                skipped.add((n, "redirect_cycle"))
        else:
            skipped.add((extra, kind))
            for s in chain:
                skipped.add((s, "unresolved"))
    return resources, aliases, edges, skipped


# ---------------------------------------------------------------------
# Fixture: 70 pages, four cycle kinds.
# ---------------------------------------------------------------------

def build_graph_70():
    pages = {}
    pages["/"] = {"body": "home", "links": [
        "/p/1", "/items", "/c/a", "/cc/1", "/ic/ext", "/r/a", "/rc/1",
        "/mc/1", "/rd/dis", "/rd/ext", "/f/1",
        "/private/secret", "https://other.test/x", "/gone", "/p/18"]}
    for i in range(1, 21):
        links = []
        if i < 20:
            links.append("/p/%d" % (i + 1))
        if i == 20:
            links.append("/p/18")          # link cycle 18->19->20->18
        if i == 1:
            links += ["/p//2", "/p/../p/2", "/p/2#frag",
                      "/item?id=2&utm=1&id=1"]
        pages["/p/%d" % i] = {"body": "p%d" % i, "links": links}
    pages["/items"] = {"body": "items", "links": [
        "/item?id=1", "/item?id=2", "/item?id=3",
        "/item?id=2&id=1", "/item?id=3#reviews"]}
    for k in ("1", "2", "3"):
        pages["/item?id=%s" % k] = {"body": "item" + k, "links": ["/"]}
    pages["/item?id=1&id=2"] = {"body": "item12",
                                "links": ["/item?id=3"]}
    pages["/c/a"] = {"body": "ca", "canonical": "/c/b",
                     "links": ["/p/9"]}
    pages["/c/b"] = {"body": "cb", "canonical": "../c/d"}
    pages["/c/d"] = {"body": "cd", "links": ["/p/1"]}
    pages["/cc/1"] = {"body": "cc1", "canonical": "/cc/2",
                      "links": ["/p/3"]}
    pages["/cc/2"] = {"body": "cc2", "canonical": "/cc/3"}
    pages["/cc/3"] = {"body": "cc3", "canonical": "/cc/1"}
    pages["/ic/ext"] = {"body": "icext",
                        "canonical": "https://other.test/x",
                        "links": ["/ic/dis"]}
    pages["/ic/dis"] = {"body": "icdis", "canonical": "/private/secret",
                        "links": ["/ic/miss"]}
    pages["/ic/miss"] = {"body": "icmiss", "canonical": "/gone"}
    pages["/private/secret"] = {"body": "blocked"}
    pages["/r/a"] = {"redirect": "/r/b"}
    pages["/r/b"] = {"redirect": "../r/c"}
    pages["/r/c"] = {"body": "rc", "links": ["/p/4"]}
    pages["/rc/1"] = {"redirect": "/rc/2"}
    pages["/rc/2"] = {"redirect": "/rc/3"}
    pages["/rc/3"] = {"redirect": "/rc/1"}
    pages["/mc/1"] = {"redirect": "/mc/2"}
    pages["/mc/2"] = {"body": "mc2", "canonical": "/mc/3"}
    pages["/mc/3"] = {"body": "mc3", "canonical": "/mc/1"}
    pages["/rd/dis"] = {"redirect": "/private/secret"}
    pages["/rd/ext"] = {"redirect": "https://other.test/x"}
    for i in range(1, 24):
        links = ["/"]
        if i < 23:
            links.append("/f/%d" % (i + 1))
        else:
            links.append("/item?id=3")
        pages["/f/%d" % i] = {"body": "f%d" % i, "links": links}
    assert len(pages) == 70, len(pages)
    return {"origin": "https://example.test", "seeds": ["/"],
            "spacing_ms": 3, "disallow": ["/private/"], "pages": pages}


def build_graph_retry():
    pages = {
        "/": {"body": "root", "links": [
            "/a", "/b", "/c", "/sec/x", "https://ext.test/"]},
        "/a": {"body": "a", "links": ["/d", "/e", "/b"]},
        "/b": {"body": "b", "retry_after_ms": 50, "links": ["/c"]},
        "/c": {"body": "c", "retry_after_ms": 130, "links": ["/b"]},
        "/d": {"body": "d-alias", "canonical": "/a"},
        "/e": {"redirect": "/f"},
        "/f": {"body": "f", "links": ["/g"]},
        "/g": {"body": "g", "retry_after_ms": 80},
        "/sec/x": {"body": "blocked"},
    }
    return {"origin": "https://t.test", "seeds": ["/"], "spacing_ms": 5,
            "disallow": ["/sec/"], "pages": pages}


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------

def run_mirror(graph_path, state_path, workers, limit=None):
    cmd = [sys.executable, str(MIRROR), str(graph_path),
           str(state_path), "--workers", str(workers)]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(Path(state_path).read_text())


def index_of(state):
    """The worker-count-invariant part of a state/output document."""
    return {k: state[k] for k in
            ("resources", "aliases", "edges", "skipped")}


def canon_groups(aliases):
    groups = {}
    for src, dst in aliases.items():
        groups.setdefault(dst, set()).add(src)
    return {k: sorted(v) for k, v in sorted(groups.items())}


def compare_to_reference(state, graph, label):
    resources, aliases, edges, skipped = reference_index(graph)
    got_res = {r["url"]: r["sha256"] for r in state["resources"]}
    assert got_res == resources, (
        "%s: resources differ only-mirror=%s only-ref=%s" % (
            label, sorted(set(got_res) - set(resources)),
            sorted(set(resources) - set(got_res))))
    assert state["aliases"] == aliases, (
        "%s: aliases differ mirror=%s ref=%s" % (
            label, state["aliases"], aliases))
    got_edges = {tuple(e) for e in state["edges"]}
    assert got_edges == edges, (
        "%s: edges differ only-mirror=%s only-ref=%s" % (
            label, sorted(got_edges - edges),
            sorted(edges - got_edges)))
    got_skip = {(s["url"], s["reason"]) for s in state["skipped"]}
    assert got_skip == skipped, (
        "%s: skipped differ only-mirror=%s only-ref=%s" % (
            label, sorted(got_skip - skipped),
            sorted(skipped - got_skip)))
    assert canon_groups(state["aliases"]) == canon_groups(aliases)


def check_requests(requests, graph, label, complete=True):
    """Per-request invariants: no blocked/external access, spacing,
    strictly increasing virtual time, Retry-After lower bounds."""
    spacing = max(1, graph.get("spacing_ms", 0))
    disallow = graph.get("disallow", [])
    pages = graph["pages"]
    prev_t, last_429, got_200 = None, {}, set()
    for r in requests:
        url, t, status = r["url"], r["t"], r["status"]
        assert url.startswith("/"), (
            "%s: external URL requested %s" % (label, url))
        assert not any(url.startswith(d) for d in disallow), (
            "%s: disallowed path requested %s" % (label, url))
        if prev_t is not None:
            assert t > prev_t, "%s: clock not increasing" % label
            assert t - prev_t >= spacing, (
                "%s: spacing violated at t=%d" % (label, t))
        if url in last_429:
            need = last_429[url] + pages[url]["retry_after_ms"]
            assert t >= need, (
                "%s: Retry-After violated for %s (%d < %d)" % (
                    label, url, t, need))
        if status == 429:
            last_429[url] = t
        else:
            got_200.add(url)
        prev_t = t
    if complete:
        for url in last_429:
            assert url in got_200, (
                "%s: %s never retried after 429" % (label, url))


# ---------------------------------------------------------------------
# Acceptance 1: 70-page graph, four cycle kinds, workers 1 vs 6.
# ---------------------------------------------------------------------

def test_graph_70():
    graph = build_graph_70()
    gpath = TMP / "graph70.json"
    gpath.write_text(json.dumps(graph))
    s1 = run_mirror(gpath, TMP / "state70-w1.json", workers=1)
    s6 = run_mirror(gpath, TMP / "state70-w6.json", workers=6)
    assert s1["done"] and s6["done"]
    # identical index under both concurrencies
    assert index_of(s1) == index_of(s6), "workers=1 vs workers=6 differ"
    # identical to the independent reference traversal
    compare_to_reference(s1, graph, "workers=1")
    compare_to_reference(s6, graph, "workers=6")
    # canonical groups agree across concurrencies
    assert canon_groups(s1["aliases"]) == canon_groups(s6["aliases"])
    # structural spot checks
    aliases = s1["aliases"]
    assert aliases["/c/a"] == "/c/d" and aliases["/c/b"] == "/c/d"
    assert aliases["/r/a"] == "/r/c" and aliases["/r/b"] == "/r/c"
    reasons = {}
    for s in s1["skipped"]:
        reasons.setdefault(s["reason"], set()).add(s["url"])
    assert reasons["canonical_cycle"] == {"/cc/1", "/cc/2", "/cc/3"}
    assert reasons["redirect_cycle"] == {
        "/rc/1", "/rc/2", "/rc/3", "/mc/1", "/mc/2", "/mc/3"}
    assert "/private/secret" in reasons["disallowed"]
    assert "https://other.test/x" in reasons["external"]
    assert "/gone" in reasons["missing"]
    assert {"/ic/ext", "/ic/dis", "/ic/miss"} <= reasons[
        "invalid_canonical"]
    res_urls = {r["url"] for r in s1["resources"]}
    assert "/private/secret" not in res_urls
    # link-cycle pages are ordinary resources
    assert {"/p/18", "/p/19", "/p/20"} <= res_urls
    # canonical-cycle members stand alone as resources
    assert {"/cc/1", "/cc/2", "/cc/3"} <= res_urls
    # query normalization: only whitelisted id, sorted
    assert "/item?id=1&id=2" in res_urls
    check_requests(s1["requests"], graph, "w1")
    check_requests(s6["requests"], graph, "w6")
    print("acceptance 1 ok: 70 pages, 4 cycle kinds, workers 1 == 6 == "
          "reference (%d resources, %d aliases, %d edges)" % (
              len(s1["resources"]), len(aliases), len(s1["edges"])))


# ---------------------------------------------------------------------
# Acceptance 2: 429 injection, restart, waiting-queue recovery.
# ---------------------------------------------------------------------

def test_retry_restart():
    graph = build_graph_retry()
    gpath = TMP / "graph-retry.json"
    gpath.write_text(json.dumps(graph))

    # uninterrupted ground-truth runs at two concurrencies
    full1 = run_mirror(gpath, TMP / "retry-full-w1.json", workers=1)
    full5 = run_mirror(gpath, TMP / "retry-full-w5.json", workers=5)
    assert full1["done"] and full5["done"]
    assert index_of(full1) == index_of(full5)
    compare_to_reference(full1, graph, "full-w1")
    check_requests(full1["requests"], graph, "full-w1")
    check_requests(full5["requests"], graph, "full-w5")
    assert any(r["status"] == 429 for r in full1["requests"])

    # crashed run: --limit cuts while 429 retries are still queued
    partial_path = TMP / "retry-partial.json"
    part = run_mirror(gpath, partial_path, workers=3, limit=4)
    assert not part["done"]
    assert len(part["requests"]) == 4
    assert part["_resume"]["waiting"], "no pending 429 retry saved"
    assert part["_resume"]["frontier"], "no pending frontier saved"
    assert any(r["status"] == 429 for r in part["requests"])
    check_requests(part["requests"], graph, "partial", complete=False)

    # resume from the same state: waiting queue must recover
    done = run_mirror(gpath, partial_path, workers=3)
    assert done["done"]
    check_requests(done["requests"], graph, "resumed")
    # nothing discovered compliantly was lost across the restart
    assert index_of(done) == index_of(full1), (
        "restarted index differs from uninterrupted run")
    compare_to_reference(done, graph, "resumed")
    # every throttled URL was eventually fetched
    retried = {r["url"] for r in done["requests"] if r["status"] == 429}
    assert retried == {"/b", "/c", "/g"}
    ok = {r["url"] for r in done["requests"] if r["status"] == 200}
    assert retried <= ok
    # blocked path was never requested, across the whole log
    assert not any(r["url"].startswith("/sec/")
                   for r in done["requests"])
    print("acceptance 2 ok: 429 + restart + waiting-queue recovery, "
          "%d requests, index == ground truth" % len(done["requests"]))


def main():
    TMP.mkdir(exist_ok=True)
    for old in TMP.glob("*.json"):
        old.unlink()
    test_graph_70()
    test_retry_restart()
    print("ALL ACCEPTANCE TESTS PASSED")


if __name__ == "__main__":
    main()
