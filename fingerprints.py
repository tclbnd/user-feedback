#!/usr/bin/env python3
"""Layered structural / content fingerprints for page change detection.

The fingerprint is deliberately layered so that a template-only redesign can
never masquerade as a content change:

* struct layer  ``fp_struct``
      canonical DOM outline of the page skeleton (tags + normalized
      attributes, document order) plus the block skeleton for every
      non-content block (id, kind, DOM path, attributes).
* chrome layer  ``fp_chrome``
      normalized text of the non-content material: chrome blocks (nav,
      sidebar, footer, ...) and static template text.
* template layer ``fp_template``
      commitment over struct + chrome; this is the full "template" layer.
* content layer ``fp_content``
      normalized text of the content region, which is located from document
      structure (semantic ids/roles, then structural containers, then a
      text-mass fallback that is reported as low confidence).

Normalization contract
----------------------
* Text is NFKC normalized, zero-width characters are removed, every Unicode
  whitespace run collapses to one ASCII space and the edges are stripped.
  Whitespace-only edits therefore cannot move a digest.
* Attributes are sorted by name, ``class`` tokens are sorted, ``style``
  declarations are sorted and values are whitespace-collapsed, so attribute
  order in HTML or in JSON can never move a digest.
* Comments, doctype and script/style text are ignored.

Chunking contract
-----------------
Block boundaries inside the content region never enter ``fp_struct`` and the
content text is space-joined before hashing, so re-chunking the extracted
content into a different number of blocks cannot change any fingerprint or
any decision.  Byte-level chunking is also irrelevant: every digest is
computed by streaming the canonical byte stream and the result is identical
for any feed size (see ``hash_stream``).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "layered-fp/1"

RS = chr(0x1E)  # record separator inside the canonical stream
US = chr(0x1F)  # unit separator inside one record


def _chars(*codepoints: int) -> str:
    """Build a string from code points, keeping this source file ASCII."""
    return "".join(chr(c) for c in codepoints)


# Unicode spaces / whitespace that editors, JSON writers and HTML entities
# like to reshuffle.  Numeric so that the source stays pure ASCII.
SPACE_CODEPOINTS = (
    0x00A0, 0x1680,
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
    0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
)
ZW_CODEPOINTS = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)

_WS_RE = re.compile("[\\s" + _chars(*SPACE_CODEPOINTS) + "]+")
_ZW_TABLE = {c: None for c in ZW_CODEPOINTS}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN_RE = re.compile("[^0-9a-z" + _chars(0x4E00) + "-" + _chars(0x9FFF) + "]+")

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
SKIP_TEXT_TAGS = {"script", "style", "noscript"}

# Chinese words for "content" / "body text", built numerically.
ZH_CONTENT_TOKENS = {
    _chars(0x6B63, 0x6587),                        # zheng wen
    _chars(0x6B63, 0x6587, 0x5185, 0x5BB9),        # zheng wen nei rong
    _chars(0x5185, 0x5BB9),                        # nei rong
}

# Token based: "article-body" matches, "mainnav" does not.
CONTENT_TOKENS = {
    "main", "article", "content", "post", "entry", "story", "text", "detail",
    "bodycopy", "articlebody", "postcontent", "entrycontent", "maincontent",
} | ZH_CONTENT_TOKENS

STRONG_CHROME_TOKENS = {
    "nav", "navigation", "menu", "menubar", "header", "footer", "aside",
    "sidebar", "widget", "widgets", "breadcrumb", "breadcrumbs", "copyright",
    "related", "recommend", "recommendation", "recommends", "comment",
    "comments", "ad", "ads", "advert", "adverts", "advertise", "banner",
    "promo", "toolbar", "search", "login", "signin", "share", "social",
    "tag", "tags", "topbar", "navbar", "masthead", "navlinks", "sitenav",
    "cookie", "consent", "newsletter", "subscribe",
}

CHROME_ROLES = {
    "navigation", "contentinfo", "banner", "complementary", "search",
    "menu", "menubar", "toolbar", "note",
}
CONTENT_ROLES = {"main", "article", "content"}
# Container words too generic to outvote a chrome word on their own.
GENERIC_CONTENT_TOKENS = {"main", "article", "content"}
SECTION_CHROME_TOKENS = {"header", "footer"}
CONTAINER_TAGS = {"main", "article"}
CONTAINER_ROLES = CONTENT_ROLES
CONTAINER_TOKENS = {
    "main", "article", "content", "post", "entry", "story", "text", "detail",
} | ZH_CONTENT_TOKENS


# ---------------------------------------------------------------------------
# canonical hashing
# ---------------------------------------------------------------------------


def hash_stream(chunks: Iterable[bytes], chunk_size: Optional[int] = None) -> str:
    """SHA-256 over a byte stream, independent of how the stream is fed.

    ``chunk_size`` forces a different feed size; the digest cannot change.
    """
    digest = hashlib.sha256()
    if chunk_size and chunk_size > 0:
        buffer = b""
        for chunk in chunks:
            buffer += chunk
            while len(buffer) >= chunk_size:
                digest.update(buffer[:chunk_size])
                buffer = buffer[chunk_size:]
        if buffer:
            digest.update(buffer)
    else:
        for chunk in chunks:
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _escape(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(RS, "\\x1e")
        .replace(US, "\\x1f")
    )


def encode_records(records: Iterable[Sequence[Any]]) -> List[bytes]:
    """Encode canonical records into a list of byte chunks (one per record)."""
    return [
        (US.join(_escape(part) for part in record) + RS).encode("utf-8", "surrogatepass")
        for record in records
    ]


def digest_records(records: Sequence[Sequence[Any]], chunk_size: Optional[int] = None) -> str:
    return hash_stream(encode_records(records), chunk_size)


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def norm_text(value: Any) -> str:
    """Canonical text shared by every fingerprint layer."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).translate(_ZW_TABLE)
    return _WS_RE.sub(" ", text).strip()


# Two ASCII/digit characters need a separator to stay words; anything else
# (CJK, punctuation, digits next to CJK) does not.  This is the rule that
# makes content hashing independent of where block boundaries fall.
_WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def norm_content_text(value: Any) -> str:
    """Canonical prose text: whitespace only survives between two words.

    The content layer must not depend on how the extractor chunked the page.
    Joining two blocks with or without a separator has to be equivalent, and
    that is only true if a whitespace run's survival depends purely on its two
    neighbouring characters.  Collapsing to a space and then dropping every
    space that is not strictly between two ASCII word characters gives that
    property while keeping English word boundaries intact.
    """
    text = norm_text(value)
    if not text:
        return ""
    out = []
    length = len(text)
    for position, char in enumerate(text):
        if char == " ":
            prev_char = text[position - 1] if position else ""
            next_char = text[position + 1] if position + 1 < length else ""
            if prev_char in _WORD and next_char in _WORD:
                out.append(" ")
            continue
        out.append(char)
    return "".join(out)


def norm_attr_value(name: str, value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False)
    else:
        raw = "" if value is None else str(value)
    text = norm_text(raw)
    if name == "class":
        return " ".join(sorted(token for token in text.split(" ") if token))
    if name == "style":
        declarations = []
        for part in text.split(";"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                prop, val = part.split(":", 1)
                prop = norm_text(prop).lower()
                if prop:
                    declarations.append(prop + ":" + norm_text(val))
            else:
                declarations.append(part)
        return "; ".join(sorted(declarations))
    return text


def norm_attrs(attrs: Any) -> Tuple[Tuple[str, str], ...]:
    """Canonical, order-independent attribute tuple."""
    if not attrs:
        return ()
    pairs: List[Tuple[str, str]] = []
    if isinstance(attrs, dict):
        items: Iterable[Any] = attrs.items()
    else:
        items = attrs
    for item in items:
        try:
            name, value = item
        except (TypeError, ValueError):
            continue
        name = norm_text(name).lower()
        if not name:
            continue
        pairs.append((name, norm_attr_value(name, value)))
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return tuple(pairs)


def attrs_to_string(attrs: Sequence[Tuple[str, str]]) -> str:
    return US.join(name + "=" + value for name, value in attrs)


def attrs_to_dict(attrs: Sequence[Tuple[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in attrs:
        out.setdefault(name, value)
    return out


def _tokens(value: Any) -> List[str]:
    camel = _CAMEL_RE.sub(r"\1 \2", norm_text(value))
    return [token for token in _TOKEN_RE.split(camel.lower()) if token]


# ---------------------------------------------------------------------------
# tolerant HTML tree
# ---------------------------------------------------------------------------


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "order")

    def __init__(self, tag: str, attrs: Tuple[Tuple[str, str], ...] = (),
                 parent: Optional["Node"] = None, order: int = -1) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: List[Any] = []
        self.parent = parent
        self.order = order


class _TreeParser(HTMLParser):
    """Tolerant parser: unknown/mismatched markup never raises."""

    def __init__(self) -> None:
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = Node("#document", order=0)
        self._stack: List[Node] = [self.root]
        self._counter = 0

    def handle_starttag(self, tag, attrs):
        declared = {norm_text(name).lower() for name, _ in attrs}
        if "doctype" in declared:
            return
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, self_closing=True)

    def _open(self, tag, attrs, self_closing: bool) -> None:
        name = (tag or "").lower()
        if not name:
            return
        self._counter += 1
        node = Node(name, norm_attrs(attrs), self._stack[-1], self._counter)
        self._stack[-1].children.append(node)
        if self_closing or name in VOID_TAGS:
            return
        self._stack.append(node)

    def handle_endtag(self, tag):
        name = (tag or "").lower()
        if name in VOID_TAGS:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == name:
                del self._stack[index:]
                return
        # Unmatched closing tag: ignored, as browsers do.

    def handle_data(self, data):
        if data:
            self._stack[-1].children.append(data)

    def handle_comment(self, data):
        return  # comments are not part of any fingerprint

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return


def parse_template(tpl: str) -> Node:
    parser = _TreeParser()
    try:
        parser.feed(tpl or "")
        parser.close()
    except Exception:  # html.parser is tolerant, but never fail a scan
        pass
    return parser.root


def iter_nodes(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node is not root:
            yield node
        for child in reversed([c for c in node.children if isinstance(c, Node)]):
            stack.append(child)


def node_path(node: Node) -> Tuple[str, ...]:
    tags: List[str] = []
    current: Optional[Node] = node
    while current is not None and current.tag != "#document":
        tags.append(current.tag)
        current = current.parent
    tags.reverse()
    return tuple(tags)


def node_label(node: Node) -> str:
    label = node.tag
    attrs = attrs_to_dict(node.attrs)
    if attrs.get("id"):
        label += "#" + attrs["id"]
    classes = attrs.get("class", "")
    if classes:
        label += "." + ".".join(classes.split(" ")[:2])
    return label


def is_container(node: Node) -> bool:
    """Structural hint that this element holds the page body text."""
    if node.tag in CONTAINER_TAGS:
        return True
    attrs = attrs_to_dict(node.attrs)
    if norm_text(attrs.get("role", "")).lower() in CONTAINER_ROLES:
        return True
    tokens = set(_tokens(attrs.get("id", ""))) | set(_tokens(attrs.get("class", "")))
    if tokens & STRONG_CHROME_TOKENS:
        return False
    return bool(tokens & CONTAINER_TOKENS)


def outermost_container(node: Optional[Node]) -> Optional[Node]:
    chosen: Optional[Node] = None
    current = node
    while current is not None and current.tag != "#document":
        if is_container(current):
            chosen = current
        current = current.parent
    return chosen


def top_section(node: Optional[Node]) -> Optional[Node]:
    """Highest element below the document root (the page's main section)."""
    if node is None:
        return None
    chosen = node
    current = node.parent
    while current is not None and current.tag != "#document":
        # Stop below <html>/<body>: those group the whole page, not a section.
        if current.tag in ("body", "html"):
            break
        chosen = current
        current = current.parent
    return chosen


class TemplateIndex:
    """Lookup tables over one parsed template.

    ``doc_seq`` assigns a monotonic document-order sequence number to every
    element and to every ``{{placeholder}}`` occurrence as it is encountered.
    That sequence is what orders prose: extracted block order never decides
    reading order, so an extraction that lists blocks back to front still
    yields the same fingerprints.
    """

    def __init__(self, root: Node) -> None:
        self.root = root
        self.by_id: Dict[str, Node] = {}
        self.placeholder_paths: Dict[str, Tuple[str, ...]] = {}
        self.placeholder_nodes: Dict[str, Node] = {}
        self.placeholder_seq: Dict[str, int] = {}
        self.node_seq: Dict[int, int] = {}
        self.static_texts: List[str] = []
        self.nodes_by_path: Dict[Tuple[str, ...], List[Node]] = {}
        self._seq = 0
        self._build()

    def _build(self) -> None:
        ordered = list(iter_nodes(self.root))
        for node in ordered:
            self.nodes_by_path.setdefault(node_path(node), []).append(node)

        # Single document-order pass.  Element and text children are visited in
        # the authored order, so a placeholder's sequence number is its real
        # reading position: the heading in <h1> comes before the paragraphs
        # that follow it, even though the same name also appears in <head>.
        # Iterative because real pages can nest thousands of levels deep.
        stack: List[Tuple[str, Any, Optional[Node]]] = [("enter", self.root, None)]
        while stack:
            kind, payload, parent = stack.pop()
            if kind == "text":
                raw = str(payload)
                for match in _PLACEHOLDER_RE.finditer(raw):
                    name = match.group(1)
                    self._seq += 1
                    # A name can appear twice (e.g. <title> and <h1>); the
                    # in-body occurrence is the one that carries the block, so
                    # the last occurrence wins.
                    self.placeholder_seq[name] = self._seq
                    self.placeholder_paths[name] = node_path(parent) if parent else ()
                    self.placeholder_nodes[name] = parent
                text = norm_text(_PLACEHOLDER_RE.sub("", raw))
                if text:
                    self.static_texts.append(text)
                continue

            node = payload
            self._seq += 1
            self.node_seq[id(node)] = self._seq
            node_id = norm_text(attrs_to_dict(node.attrs).get("id", ""))
            if node_id:
                self.by_id.setdefault(node_id, node)
            for child in reversed(node.children):
                if isinstance(child, Node):
                    if child.tag in SKIP_TEXT_TAGS:
                        self._seq += 1
                        self.node_seq[id(child)] = self._seq
                        continue
                    stack.append(("enter", child, node))
                else:
                    stack.append(("text", child, node))

    def node_for_block(self, block_id: str) -> Optional[Node]:
        if block_id in self.by_id:
            return self.by_id[block_id]
        if block_id in self.placeholder_nodes:
            return self.placeholder_nodes[block_id]
        path = self.placeholder_paths.get(block_id)
        if path is not None:
            candidates = self.nodes_by_path.get(path)
            if candidates:
                return candidates[-1]
        return None


# ---------------------------------------------------------------------------
# block model
# ---------------------------------------------------------------------------


def block_id(block: Dict[str, Any]) -> str:
    return norm_text(block.get("id", ""))


def block_text(block: Dict[str, Any]) -> str:
    return norm_text(block.get("text", ""))


def block_attrs(block: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    return norm_attrs(block.get("attrs"))


def block_kind(block: Dict[str, Any]) -> str:
    """Classify a block as content / chrome / neutral.

    A block counts as content only when something specific says so: an
    explicit role, or a content word such as ``article-body`` / ``post``.
    A chrome word vetoes a generic article word, so ``article-header`` stays
    chrome while ``article-body`` is content.  Neutral is the default: an
    unlabelled block is prose until proven otherwise.
    """
    attrs = attrs_to_dict(block_attrs(block))
    role = norm_text(attrs.get("role", "") or attrs.get("data-role", "")).lower()
    if role in CONTENT_ROLES:
        return "content"
    if role in CHROME_ROLES:
        return "chrome"
    tokens = set(_tokens(block_id(block)))
    if tokens & STRONG_CHROME_TOKENS or tokens & SECTION_CHROME_TOKENS:
        return "chrome"
    return "neutral"


# ---------------------------------------------------------------------------
# content-region location
# ---------------------------------------------------------------------------

# Base confidence of each location strategy.  Recorded in the evidence so
# nothing downstream has to guess how much to trust the region.
METHOD_CONFIDENCE = {
    "declared+container": 0.98,
    "declared": 0.90,
    "dom_container": 0.86,
    "text_mass": 0.55,
    "none": 0.30,
}


def locate_content(blocks: Sequence[Dict[str, Any]],
                   nodes: Sequence[Optional[Node]]) -> Tuple[List[bool], Dict[str, Any]]:
    """Return content flags per block plus a fully auditable region record."""
    count = len(blocks)
    kinds = [block_kind(block) for block in blocks]
    flags = [False] * count
    region: Dict[str, Any] = {
        "method": "none",
        "container": None,
        "block_ids": [],
        "excluded_chrome": [block_id(b) for b, k in zip(blocks, kinds) if k == "chrome"],
        "candidates": [],
        "ambiguous": False,
        "unmapped_blocks": sum(1 for node in nodes if node is None),
        "confidence": METHOD_CONFIDENCE["none"],
        "mass_chars": 0,
        "total_chars": sum(len(block_text(b)) for b in blocks),
    }
    if not blocks:
        region["method"] = "empty"
        region["confidence"] = 1.0
        return flags, region

    containers: Dict[int, Dict[str, Any]] = {}
    for index, node in enumerate(nodes):
        container = outermost_container(node)
        if container is None:
            continue
        entry = containers.setdefault(id(container), {
            "container": container,
            "indices": [],
            "seed_indices": [],
            "mass": 0,
        })
        entry["indices"].append(index)
        if kinds[index] == "content":
            entry["seed_indices"].append(index)
        if kinds[index] != "chrome":
            entry["mass"] += len(block_text(blocks[index]))

    for entry in containers.values():
        entry["seed_mass"] = sum(len(block_text(blocks[i])) for i in entry["seed_indices"])

    region["candidates"] = [
        {
            "container": node_label(entry["container"]),
            "blocks": len(entry["indices"]),
            "content_blocks": len(entry["seed_indices"]),
            "chars": entry["mass"],
            "seed_chars": entry["seed_mass"],
        }
        for entry in sorted(containers.values(), key=lambda e: e["container"].order)
    ]

    def rank(entry: Dict[str, Any]) -> Tuple[int, int, int]:
        return (entry["seed_mass"], entry["seed_indices"] and 1 or 0, entry["mass"])

    best: Optional[Dict[str, Any]] = None
    if containers:
        ranked = sorted(containers.values(), key=lambda e: (rank(e), -e["container"].order),
                        reverse=True)
        best = ranked[0]
        if best["seed_indices"]:
            region["method"] = "declared+container"
        else:
            region["method"] = "dom_container"
        if len(ranked) > 1:
            runner_up = ranked[1]
            if rank(runner_up)[0] > 0 and rank(runner_up)[0] >= 0.6 * max(1, rank(best)[0]):
                region["ambiguous"] = True
            elif not best["seed_indices"] and runner_up["mass"] >= 0.6 * max(1, best["mass"]):
                region["ambiguous"] = True
        for index in best["indices"]:
            if kinds[index] != "chrome":
                flags[index] = True
        region["container"] = node_label(best["container"])

    for index, kind in enumerate(kinds):
        if kind == "content" and not flags[index]:
            # Declared content without a locatable container (e.g. a block id
            # that only exists in the extraction, not in the skeleton).
            flags[index] = True
            if region["method"] in ("none", "dom_container"):
                region["method"] = "declared"
                region["ambiguous"] = True

    if not any(flags):
        # Final fallback.  Group blocks by their *top-level section* and pick
        # whole sections, never individual blocks: block-level picking would
        # make the region depend on how the extractor chunked the page.  A
        # section is the highest ancestor of a block that is not the page
        # root, so a heading and its paragraphs stay together even when they
        # live in nested elements.
        groups: Dict[int, Dict[str, Any]] = {}
        for index, node in enumerate(nodes):
            if kinds[index] == "chrome":
                continue
            text = block_text(blocks[index])
            if not text:
                continue
            anchor = top_section(node)
            key = id(anchor) if anchor is not None else -1
            entry = groups.setdefault(key, {
                "node": anchor,
                "indices": [],
                "chars": 0,
                "order": anchor.order if anchor is not None else 10 ** 9 + index,
            })
            entry["indices"].append(index)
            entry["chars"] += len(text)
        ranked = sorted(groups.values(), key=lambda e: (-e["chars"], e["order"]))
        if ranked:
            best = ranked[0]
            for index in best["indices"]:
                flags[index] = True
            region["method"] = "text_mass"
            region["container"] = node_label(best["node"]) if best["node"] is not None else None
            region["ambiguous"] = (len(ranked) > 1
                                   and ranked[1]["chars"] >= 0.6 * max(1, best["chars"]))
            region["candidates"] = [
                {"container": node_label(e["node"]) if e["node"] is not None else "",
                 "blocks": len(e["indices"]), "chars": e["chars"]}
                for e in ranked
            ]
        else:
            region["method"] = "none"

    if region["method"] == "empty":
        region["method"] = "none"

    # Blocks the skeleton cannot place (no element id, no placeholder) cannot
    # be attributed to markup, so they must never leak into the template
    # layer.  Anything not explicitly chrome joins the content layer instead.
    unmapped_included = []
    for position, node in enumerate(nodes):
        if node is None and kinds[position] != "chrome" and not flags[position]:
            flags[position] = True
            unmapped_included.append(block_id(blocks[position]))
    region["unmapped_included"] = unmapped_included

    base = METHOD_CONFIDENCE.get(region["method"], 0.5)
    if region["method"] == "dom_container" and region["ambiguous"]:
        base -= 0.10
    elif region["method"] in ("declared+container", "dom_container") and region["ambiguous"]:
        base -= 0.06
    if region["unmapped_blocks"]:
        base -= min(0.10, 0.02 * region["unmapped_blocks"])
    region["confidence"] = round(max(0.05, min(1.0, base)), 4)
    region["block_ids"] = [block_id(blocks[i]) for i in range(count) if flags[i]]
    region["mass_chars"] = sum(len(block_text(blocks[i])) for i in range(count) if flags[i])
    return flags, region


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


def _outline_records(root: Node) -> List[Tuple[str, str, str]]:
    """DOM skeleton: element order and attributes, with explicit end markers.

    Iterative on purpose: the depth of a real page is not bounded by our
    recursion budget.
    """
    records: List[Tuple[str, str, str]] = []
    stack: List[Tuple[Any, ...]] = []
    for child in reversed(root.children):
        if isinstance(child, Node) and child.tag not in SKIP_TEXT_TAGS:
            stack.append(("open", child))
    while stack:
        entry = stack.pop()
        if entry[0] == "end":
            records.append(("end", entry[1].tag, ""))
            continue
        node = entry[1]
        records.append(("elem", node.tag, attrs_to_string(node.attrs)))
        stack.append(("end", node))
        for child in reversed(node.children):
            if isinstance(child, Node) and child.tag not in SKIP_TEXT_TAGS:
                stack.append(("open", child))
    return records


def _block_skeleton(blocks: Sequence[Dict[str, Any]], kinds: Sequence[str],
                    flags: Sequence[bool], paths: Sequence[Optional[Tuple[str, ...]]]) -> List[Tuple[str, ...]]:
    """Skeleton records for the block layer.

    Content blocks contribute nothing beyond a presence marker: their ids,
    count and location strategy are absent, so re-chunking the content cannot
    move fp_struct.  Non-content blocks contribute their id and kind, sorted so
    extraction order cannot move the digest.

    Only *explicitly typed* chrome blocks appear here.  A neutral block is
    prose by default: the extractor produced text we cannot prove is chrome,
    so a change in it must be able to reach the content layer rather than
    silently becoming a template change.  The DOM path is not recorded either,
    because a rearrangement of template chrome (e.g. nav moved below the body)
    is a chrome-text question, already covered by fp_chrome.
    """
    records: List[Tuple[str, ...]] = []
    has_content = False
    for index, keep in enumerate(flags):
        if keep:
            has_content = True
            continue
        if kinds[index] != "chrome":
            records.append(("block", block_id(blocks[index]), kinds[index]))
    if has_content:
        records.append(("content-region",))
    records.sort()
    return records


# ---------------------------------------------------------------------------
# page model
# ---------------------------------------------------------------------------


class PageModel:
    """Everything the fingerprints need, computed once per snapshot."""

    def __init__(self, page: Dict[str, Any]) -> None:
        if not isinstance(page, dict):
            raise ValueError("page document must be a JSON object")
        tpl = page.get("tpl", "") or ""
        if not isinstance(tpl, str):
            raise ValueError("page 'tpl' must be a string")
        blocks = page.get("blocks", [])
        if blocks is None:
            blocks = []
        if not isinstance(blocks, list):
            raise ValueError("page 'blocks' must be a list")
        for position, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise ValueError("blocks[%d] must be an object" % position)
            if not isinstance(block.get("id", ""), str):
                raise ValueError("blocks[%d].id must be a string" % position)

        self.page = page
        self.blocks = blocks
        self.block_ids = [block_id(block) for block in blocks]
        self.block_texts = [block_text(block) for block in blocks]
        self.kinds = [block_kind(block) for block in blocks]
        self.index = TemplateIndex(parse_template(tpl))
        self.nodes = [self.index.node_for_block(bid) for bid in self.block_ids]
        self.flags, self.region = locate_content(blocks, self.nodes)
        self.paths = [node_path(node) if node is not None else None for node in self.nodes]
        # Reading order comes from the document, never from extraction order.
        # A placeholder occurrence has the sharpest position; otherwise use the
        # element that carries the block; a block the skeleton cannot place is
        # ordered by its own text, so reversing an extraction listing cannot
        # change reading order.
        self.order = []
        for position, bid in enumerate(self.block_ids):
            if bid in self.index.placeholder_seq:
                self.order.append((0, self.index.placeholder_seq[bid], ""))
                continue
            node = self.nodes[position]
            if node is not None:
                self.order.append((0, self.index.node_seq.get(id(node), 0), ""))
            else:
                self.order.append((1, 0, self.block_texts[position]))
        # Chrome layer = text we can prove is chrome (explicitly typed chrome
        # blocks in document order) plus static template text.  A neutral block
        # that the locator left outside the region is not proven chrome, so it
        # must not be able to mask a prose edit.
        self.content_text = self.layer_text(True)
        self.chrome_text = norm_content_text(" ".join(
            [norm_content_text(" ".join(
                self.block_texts[i] for i in self._chrome_positions()))
             ] + self.index.static_texts))
        self.page_text = self.layer_text(None)
        self.outline = _outline_records(self.index.root)
        self.skeleton = _block_skeleton(blocks, self.kinds, self.flags, self.paths)

    def _chrome_positions(self) -> List[int]:
        positions = [i for i in range(len(self.blocks)) if self.kinds[i] == "chrome"]
        positions.sort(key=lambda i: (self.order[i], i))
        return positions

    def layer_text(self, keep: Optional[bool]) -> str:
        """Canonical text of the content layer, chrome layer, or the whole page."""
        selected = [i for i in range(len(self.blocks))
                    if keep is None or bool(self.flags[i]) == keep]
        selected.sort(key=lambda i: (self.order[i], i))
        return norm_content_text(" ".join(self.block_texts[i] for i in selected))

    def struct_records(self) -> List[Tuple[str, ...]]:
        return (
            [("docattrs", attrs_to_string(norm_attrs(self.page.get("attrs"))), "")]
            + list(self.outline)
            + [tuple(record) for record in self.skeleton]
        )

    def digests(self, chunk_size: Optional[int] = None) -> Dict[str, str]:
        struct_records = self.struct_records()
        fp_struct = digest_records(struct_records, chunk_size)
        fp_chrome = digest_records([("chrome", self.chrome_text)], chunk_size)
        fp_content = digest_records([("content", self.content_text)], chunk_size)
        fp_template = digest_records(
            [("struct", fp_struct), ("chrome", fp_chrome)], chunk_size)
        digest = digest_records([
            ("fp_struct", fp_struct),
            ("fp_chrome", fp_chrome),
            ("fp_content", fp_content),
        ], chunk_size)
        return {
            "fp_struct": fp_struct,
            "fp_chrome": fp_chrome,
            "fp_template": fp_template,
            "fp_content": fp_content,
            "digest": digest,
        }


def fingerprint(page: Dict[str, Any], chunk_size: Optional[int] = None) -> Dict[str, Any]:
    """Compute every fingerprint layer for one page document.

    ``chunk_size`` only changes how the canonical byte stream is fed to
    SHA-256; no digest can change because of it.
    """
    model = PageModel(page)
    digests = model.digests(chunk_size)
    return {
        "schema": SCHEMA,
        "fp_struct": digests["fp_struct"],
        "fp_chrome": digests["fp_chrome"],
        "fp_template": digests["fp_template"],
        "fp_content": digests["fp_content"],
        "digest": digests["digest"],
        "region": model.region,
        "content_text": model.content_text,
        "chrome_text": model.chrome_text,
        "page_text": model.page_text,
        "content_block_ids": model.region["block_ids"],
        "blocks": [{
            "id": model.block_ids[i],
            "kind": model.kinds[i],
            "content": bool(model.flags[i]),
            "path": "/".join(model.paths[i]) if model.paths[i] else "",
            "chars": len(model.block_texts[i]),
        } for i in range(len(model.blocks))],
        "skeleton": [list(record) for record in model.struct_records()],
        "static_texts": model.index.static_texts,
    }


__all__ = [
    "SCHEMA",
    "fingerprint",
    "PageModel",
    "locate_content",
    "block_kind",
    "norm_text",
    "norm_content_text",
    "norm_attr_value",
    "norm_attrs",
    "hash_stream",
    "digest_records",
    "METHOD_CONFIDENCE",
]


