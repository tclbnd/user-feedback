#!/usr/bin/env python3
"""Deterministic de-duplication clusters for canonical + hreflang graphs.

Usage:
    python3 canon.py input.json output.json

Input (see README.md)::

    {"pages": [{"url": u, "canonical": u|null,
                "hreflang": [{"lang": "en", "url": v}, ...]}, ...],
     "langs": ["en", "ja"], "seed": 1}

Decision rules
--------------
canonical
    Every page has at most one canonical target, so the canonical links form
    a functional graph: each component ends either in a terminal page or in
    exactly one cycle.  A cycle is arbitrated by taking the lexicographically
    smallest URL of the cycle as the component's representative, so a
    canonical loop can never leave pages split across "who claims whom first".
    Cross-domain canonical targets are honoured unchanged.

hreflang
    A declaration ``A: lang=L -> B`` is usable only when B declares a target
    back to A (mutual reference).  One-way declarations, declarations to
    targets outside the input, and malformed entries are recorded as
    conflicts on the declaring page and never contribute a cluster edge.
    Mutual pairs merge their pages even when the two sides spell the language
    differently (noted as a lang mismatch) and even when the two sides sit in
    different canonical groups: the language variants of one piece of
    content end in one cluster.

clusters
    Canonical groups and usable hreflang pairs are merged; the cluster
    representative is the lexicographically smallest member URL.  Every
    decision is derived from URL / language values only, so the result is
    independent of input order and of the order in which contradictory
    declarations are listed.

langs
    ``langs`` is validated but never influences the result.  Cluster
    membership is derived from canonical and hreflang links alone, so the
    cluster summary and digest are identical under every language
    configuration.  Per-page verdicts report the language tags each page
    declares about itself (or, failing that, the tags a reciprocal partner
    assigns to it).
"""

import hashlib
import json
import sys


def _clean_url(value):
    """Return a trimmed non-empty URL string, or None."""
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _norm_lang(value):
    """Normalise a language tag: case-insensitive, ``_`` equals ``-``."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace("_", "-")
    while "--" in value:
        value = value.replace("--", "-")
    value = value.strip("-")
    return value or None


def _read_langs(raw_langs):
    """Validate the language configuration.

    The configuration is accepted for schema compatibility and checked for
    well-formedness, but it deliberately does not influence the result:
    cluster membership comes from canonical and hreflang links only.  That is
    what makes the cluster summary identical under every ``langs`` setting.
    """
    if raw_langs is None:
        return []
    if not isinstance(raw_langs, list):
        raise ValueError("input.langs must be a list")
    langs = set()
    for index, value in enumerate(raw_langs):
        lang = _norm_lang(value)
        if lang is None:
            raise ValueError("langs[%d] must be a non-empty string" % index)
        langs.add(lang)
    return sorted(langs)


class _DSU(object):
    """Union-find whose root is always the lexicographically smallest URL."""

    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        parent = self.parent
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != item:
            nxt = parent[item]
            parent[item] = root
            item = nxt
        return root

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def _normalise_pages(raw_pages):
    """Merge duplicate URL records; return (sorted urls, records)."""
    if not isinstance(raw_pages, list):
        raise ValueError("input.pages must be a list")

    urls = []
    seen = set()
    records = {}

    for index, raw in enumerate(raw_pages):
        if not isinstance(raw, dict):
            raise ValueError("pages[%d] must be an object" % index)
        url = _clean_url(raw.get("url"))
        if url is None:
            raise ValueError("pages[%d].url must be a non-empty string" % index)

        if url not in seen:
            seen.add(url)
            urls.append(url)
            records[url] = {
                "canonical_decls": set(),
                "hreflang": set(),
                "malformed": set(),
            }

        record = records[url]
        record["canonical_decls"].add(_clean_url(raw.get("canonical")))

        hreflang = raw.get("hreflang")
        if hreflang is None:
            continue
        if not isinstance(hreflang, list):
            record["malformed"].add("conflict:hreflang-not-a-list")
            continue
        for entry in hreflang:
            if not isinstance(entry, dict):
                record["malformed"].add("conflict:hreflang-malformed-entry")
                continue
            lang = _norm_lang(entry.get("lang"))
            target = _clean_url(entry.get("url"))
            if lang is None or target is None:
                record["malformed"].add("conflict:hreflang-malformed-entry")
                continue
            record["hreflang"].add((lang, target))

    urls.sort()
    return urls, records


def _resolve_canonical(urls, edges):
    """Resolve every canonical chain.

    Returns ``(root, cycles)``:

    * ``root[u]`` is the terminal URL of u's canonical component (a target
      outside the input is a valid terminal) or, for a component containing a
      cycle, the smallest URL of that cycle;
    * ``cycles[u]`` is the sorted cycle tuple for every page whose component
      contains a cycle, including pages that merely feed into it.
    """
    state = {}          # absent = unseen, 1 = on current path, 2 = resolved
    root = {}
    cycles = {}
    component_cycle = {}

    for start in urls:
        if state.get(start) == 2:
            continue

        path = []
        position = {}
        node = start

        while True:
            node_state = state.get(node, 0)

            if node_state == 2:
                resolved_root = root[node]
                resolved_cycle = component_cycle.get(resolved_root)
                for item in path:
                    state[item] = 2
                    root[item] = resolved_root
                    if resolved_cycle is not None:
                        cycles[item] = resolved_cycle
                break

            if node_state == 1:
                # Only path nodes can be in state 1: each walk resolves
                # completely before the next start is considered.
                cycle_nodes = tuple(sorted(path[position[node]:]))
                resolved_root = cycle_nodes[0]
                component_cycle[resolved_root] = cycle_nodes
                for item in path:
                    state[item] = 2
                    root[item] = resolved_root
                    cycles[item] = cycle_nodes
                break

            state[node] = 1
            position[node] = len(path)
            path.append(node)

            target = edges.get(node)
            if target is None:
                for item in path:
                    state[item] = 2
                    root[item] = node
                break
            node = target

    return root, cycles


def cluster(request):
    """Cluster one request object and return the serialisable result."""
    if not isinstance(request, dict):
        raise ValueError("input must be a JSON object")

    urls, records = _normalise_pages(request.get("pages", []))
    _read_langs(request.get("langs"))  # validated; must not affect the result

    # ---- canonical edges and their conflicts ----------------------------
    edges = {}
    canonical_tokens = {url: set() for url in urls}

    for url in urls:
        raw_decls = records[url]["canonical_decls"]
        targets = sorted(
            target
            for target in raw_decls
            if target is not None and target != url
        )
        if url in raw_decls and targets:
            # "I am canonical" plus "that page is canonical" is contradictory;
            # the external target wins deterministically and is reported.
            canonical_tokens[url].add(
                "conflict:canonical-self-and-target:" + "|".join(targets)
            )
        if not targets:
            # null, absent, or a self-reference: no usable canonical edge.
            continue
        edges[url] = targets[0]
        if len(set(targets)) > 1:
            # Only possible through duplicated page records.
            canonical_tokens[url].add(
                "conflict:canonical-multiple:" + "|".join(sorted(set(targets)))
            )
        if targets[0] not in records:
            # Not a conflict: a canonical outside the input is simply the end
            # of the component and stays unmerged with any page.
            canonical_tokens[url].add("note:canonical-external:" + targets[0])

    root, cycles = _resolve_canonical(urls, edges)

    # ---- base graph: pages first, then canonical groups ------------------
    dsu = _DSU(urls)
    by_canonical_root = {}
    for url in urls:
        by_canonical_root.setdefault(root[url], []).append(url)
    for members in by_canonical_root.values():
        first = members[0]
        for other in members[1:]:
            dsu.union(first, other)

    # ---- hreflang reciprocity --------------------------------------------
    # A declaration (A, L -> B) counts only when B points back at A.  One-way
    # and dangling declarations are reported per page and never merge pages.
    # Reciprocal declarations merge, including across canonical groups and
    # across domains, so all language variants of one piece of content end up
    # in a single cluster.
    declarations = {url: set(records[url]["hreflang"]) for url in urls}
    valid_tokens = {url: set() for url in urls}
    self_tokens = {url: set() for url in urls}
    conflict_tokens = {url: set(records[url]["malformed"]) for url in urls}
    # Reporting only: the language tags a page declares about itself, and the
    # tags a reciprocal partner assigns to it.
    self_langs = {url: set() for url in urls}
    inbound_langs = {url: set() for url in urls}

    for url in urls:
        # One declared language pointing at several *different* alternates is
        # ambiguous.  A self-reference next to one alternate is normal
        # hreflang practice and is not flagged.  Reciprocal pairs are still
        # evaluated in every case; the clash is reported only.
        alternates_by_lang = {}
        for lang, target in sorted(declarations[url]):
            if target != url:
                alternates_by_lang.setdefault(lang, set()).add(target)
        for lang in sorted(alternates_by_lang):
            targets = sorted(alternates_by_lang[lang])
            if len(targets) > 1:
                conflict_tokens[url].add(
                    "conflict:hreflang-lang-duplicate:%s->%s"
                    % (lang, "|".join(targets))
                )

        for lang, target in sorted(declarations[url]):
            if target == url:
                # Explicit self hreflang is valid and creates no edge.
                self_tokens[url].add("hreflang-self:" + lang)
                self_langs[url].add(lang)
                continue

            if target not in declarations:
                # Reciprocity is unverifiable for a target outside the input.
                conflict_tokens[url].add(
                    "conflict:hreflang-external-target:%s->%s" % (lang, target)
                )
                continue

            back_langs = sorted(
                back_lang
                for back_lang, back_target in declarations[target]
                if back_target == url
            )
            if not back_langs:
                conflict_tokens[url].add(
                    "conflict:hreflang-one-way:%s->%s" % (lang, target)
                )
                continue

            # Mutual reference: one cluster, whatever the two sides call the
            # language.  The two sides can legitimately label each other with
            # different tags, so a discrepancy is reported, not enforced: the
            # reference proves the alternates belong together, and a cluster
            # must not split because of a labelling difference.
            dsu.union(url, target)
            inbound_langs[target].add(lang)
            if lang in back_langs:
                valid_tokens[url].add("hreflang:%s<->%s" % (lang, target))
            else:
                valid_tokens[url].add(
                    "hreflang:%s<->%s(lang-mismatch:back=%s)"
                    % (lang, target, ",".join(back_langs))
                )

    # ---- clusters --------------------------------------------------------
    groups = {}
    for url in urls:
        groups.setdefault(dsu.find(url), []).append(url)

    clusters = []
    for members in groups.values():
        members = sorted(members)
        clusters.append({"rep": members[0], "members": members})
    clusters.sort(key=lambda item: (item["rep"], item["members"]))

    # Language dimension per cluster: a page contributes the tags it declares
    # about itself, or - when it has no self-reference - the tags its
    # reciprocal partners assign to it.  Reported alongside the members.
    page_langs = {}
    for url in urls:
        page_langs[url] = sorted(self_langs[url] or inbound_langs[url])

    # ---- per-page verdicts ----------------------------------------------
    verdicts = {}
    for url in urls:
        rep = dsu.find(url)
        parts = ["cluster:" + rep]

        if url in edges:
            parts.append("canonical->" + root[url])
        else:
            parts.append("canonical-self")

        resolved_cycle = cycles.get(url)
        if resolved_cycle is not None:
            parts.append(
                "cycle-min:[%s]->%s"
                % (",".join(resolved_cycle), resolved_cycle[0])
            )

        if page_langs[url]:
            parts.append("lang:" + "|".join(page_langs[url]))

        parts.extend(sorted(canonical_tokens[url]))
        parts.extend(sorted(valid_tokens[url]))
        parts.extend(sorted(self_tokens[url]))
        parts.extend(sorted(conflict_tokens[url]))
        verdicts[url] = ";".join(parts)

    # Cluster summary digest: SHA-1 over the stable Python representation of
    # the sorted cluster list (the format the original tool emitted).  The
    # list is built from sorted members and is sorted by representative, so
    # the digest is independent of input order and language configuration.
    digest = hashlib.sha1(str(clusters).encode("utf-8")).hexdigest()

    return {
        "clusters": clusters,
        "verdicts": {url: verdicts[url] for url in sorted(verdicts)},
        "digest": digest,
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        sys.stderr.write("usage: python3 canon.py input.json output.json\n")
        return 2

    input_path, output_path = args
    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            request = json.load(handle)
        result = cluster(request)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write("canon.py: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
