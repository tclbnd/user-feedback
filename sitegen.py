"""Deterministic generator for the 70-page acceptance site graph.

The graph embeds the four required cycle classes:

  1. link ring        -- ordinary <a> links forming a cycle (not aliases);
  2. redirect self-loop   -- a page redirecting to itself;
  3. redirect multi-ring  -- two pages redirecting at each other;
  4. canonical ring       -- a two-page canonical pair plus a self canonical.

It also exercises query whitelisting, dot-segment / repeated-slash
normalization, invalid canonicals (external, disallowed, missing), external
links, robots disallow and Retry-After (429) throttling.
"""
import json

ORIGIN = "https://example.test"
SPACING_MS = 5
DISALLOW = ["/private/"]


def build_graph():
    pages = {}

    def add(path, **fields):
        assert path not in pages, path
        pages[path] = fields

    # -- 1..40: ordinary content pages, chained with cross-links ---------- #
    for i in range(1, 41):
        links = []
        if i < 40:
            links.append("/p/%d" % (i + 1))
        if i > 1:
            links.append("/p/%d" % (i - 1))
        if i % 10 == 0:
            links.append("/")  # link ring contribution: back to home
        add("/p/%d" % i, body="page-%d" % i, links=links)

    # -- home / hub ------------------------------------------------------- #
    add("/", body="home", links=[
        "/p/1",
        "/dir/",
        "/about",
        "/dup?z=junk&id=2&id=1",
        "/go/about",
        "/loop-self",
        "/ring-a",
        "/canon-a",
        "/selfcanon",
        "/throttle",
        "/missing-link",
        "https://other.test/x",
        "http://example.test:8080/port",
        "/private/secret",
        "/norm/./a/../b//c",
        "/rel-target?keep=1&id=7&utm_no=9",
    ])

    # -- directory-style and extra pages ---------------------------------- #
    add("/dir/", body="dir-index", links=["sub", "../about"])
    add("/dir/sub", body="dir-sub")
    add("/norm/b/c", body="normalized")
    add("/rel-target?id=7", body="rel-target")

    # -- duplicate page declaring a canonical to a real page -------------- #
    add("/dup?id=1&id=2",
        body="duplicate",
        links=["/p/3?utm_source=x&id=9"],
        canonical="/p/3?id=9")
    add("/p/3?id=9", body="page-3-query")

    # -- redirects: valid, relative, disallowed, external, missing -------- #
    add("/go/about", redirect="/about")
    add("/go/rel", redirect="about")
    add("/go/p1", redirect="/p/1")
    add("/go/blocked", redirect="/private/secret")
    add("/go/outside", redirect="https://other.test/y")
    add("/go/ghost", redirect="/no-such-page")
    add("/about", body="about", links=["/p/40", "/go/about", "/go/rel",
                                       "/go/p1", "/go/blocked",
                                       "/go/outside", "/go/ghost"])
    add("/loop-self", redirect="/loop-self")

    # -- cycle class 3: two-node redirect ring ---------------------------- #
    add("/ring-a", redirect="/ring-b")
    add("/ring-b", redirect="/ring-a")

    # -- cycle class 4: canonical pair + self canonical ------------------- #
    add("/canon-a", body="canon-a", canonical="/canon-b")
    add("/canon-b", body="canon-b", canonical="/canon-a")
    add("/selfcanon", body="self-canon", canonical="/selfcanon")

    # -- invalid canonicals ------------------------------------------------ #
    add("/badcanon/external", body="bc1",
        canonical="https://other.test/c")
    add("/badcanon/disallowed", body="bc2",
        canonical="/private/secret")
    add("/badcanon/missing", body="bc3", canonical="/ghost")

    # -- throttled page (first visit 429, second visit the body) ---------- #
    add("/throttle", body="throttled-body",
        links=["/p/40"], retry_after_ms=25)

    # -- disallowed pages (never requestable) ----------------------------- #
    add("/private/secret", body="secret")
    add("/private/nested", body="nested")

    # -- extra reachable pages (dot-segment links, sibling resolution) ---- #
    add("/extra/a", body="extra-a", links=["b", "../about"])
    add("/extra/b", body="extra-b", links=["./a"])
    add("/extra/c", body="extra-c", links=["/extra/a", "/extra/b"])
    add("/hub-bad", body="hub-bad", links=[
        "/badcanon/external", "/badcanon/disallowed", "/badcanon/missing",
        "/extra/c"])

    assert len(pages) == 70, len(pages)
    return {
        "origin": ORIGIN,
        "seeds": ["/", "/hub-bad"],
        "spacing_ms": SPACING_MS,
        "disallow": DISALLOW,
        "pages": pages,
    }


if __name__ == "__main__":
    print(json.dumps(build_graph(), indent=2, sort_keys=True))
