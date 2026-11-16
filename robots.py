"""Robots.txt evaluator.

Usage:
    python3 robots.py input.json output.json
"""

import argparse
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _clean_robots_text(text):
    """Drop a leading UTF-8 BOM if the caller did not strip it already."""
    if text.startswith(chr(0xFEFF)):
        text = text[1:]
    return text


def _split_lines(text):
    """Split on CRLF, CR or LF only, not on exotic Unicode separators.

    Line numbers in the output must match the physical lines of the robots.txt
    document, so the split is explicit.  A trailing newline does not introduce
    an extra empty line.
    """
    if not text:
        return []
    lines = _LINE_BREAK.split(text)
    if text[-1] in "\r\n" and lines and lines[-1] == "":
        lines.pop()
    return lines


def _split_field(stripped_line):
    """Split ``field: value``.

    Returns ``(lower-case field, value)`` or ``(None, None)``.
    """
    if ":" not in stripped_line:
        return None, None
    raw_field, value = stripped_line.split(":", 1)
    field = raw_field.strip().lower()
    if not field:
        return None, None
    return field, value.strip()


def _parse_delay(value):
    """Parse a Crawl-delay value as a non-negative finite number."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return delay


def _split_pattern(pattern):
    """Split a robots pattern into literal segments around its ``*`` wildcards.

    Returns ``(segments, anchored)``.  A trailing ``$`` anchors the pattern to
    the end of the path.  Only that final ``$`` is special; any other ``$`` is
    an ordinary character.  No other character is special either.
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    return body.split("*"), anchored


def _matches(segments, anchored, target):
    """Glob-match ``target`` against literal segments joined by ``*``.

    Without an anchor the pattern is a prefix match: the target must *begin*
    with something the pattern describes.  With ``$`` the whole target must be
    consumed.  Middle segments are matched at their earliest possible position,
    which is the greedy strategy that leaves the most room for what follows.
    """
    if len(segments) == 1:
        literal = segments[0]
        return target == literal if anchored else target.startswith(literal)

    first = segments[0]
    if not target.startswith(first):
        return False
    pos = len(first)

    for segment in segments[1:-1]:
        found = target.find(segment, pos)
        if found < 0:
            return False
        pos = found + len(segment)

    last = segments[-1]
    if anchored:
        start = len(target) - len(last)
        return start >= pos and target[start:] == last
    return target.find(last, pos) >= 0


class Group:
    def __init__(self):
        self.agents = []          # (casefolded, original) pairs, in order
        self.rules = []           # parsed Allow/Disallow entries
        self.delays = []          # valid non-negative Crawl-delay values

    def add_agent(self, identifier):
        self.agents.append((identifier.casefold(), identifier))

    @property
    def representative(self):
        return self.agents[0][1] if self.agents else None

    def has_agent(self, folded):
        return any(folded == value for value, _ in self.agents)


def _parse(robots_text):
    """Parse robots.txt into (groups, sitemaps, skipped line numbers)."""
    text = _clean_robots_text(robots_text or "")
    groups = []
    sitemaps = []
    skipped = []

    current = None
    # A User-agent line opens a new group once the current group already has
    # a group-scoped directive (rule or crawl-delay).  Consecutive User-agent
    # lines therefore share one group.
    group_has_content = False

    for lineno, raw_line in enumerate(_split_lines(text), 1):
        content = raw_line.split("#", 1)[0].strip()
        if not content:
            skipped.append(lineno)
            continue

        field, value = _split_field(content)
        if field is None:
            skipped.append(lineno)
            continue

        if field == "user-agent":
            if not value:
                skipped.append(lineno)
                continue
            if current is None or group_has_content:
                current = Group()
                groups.append(current)
                group_has_content = False
            current.add_agent(value)
            continue

        if field in ("allow", "disallow"):
            if not value:
                # Empty rule values carry no meaning; record and ignore.
                skipped.append(lineno)
                continue
            if current is None:
                # A rule with no User-agent context cannot be applied.
                skipped.append(lineno)
                continue
            segments, anchored = _split_pattern(value)
            current.rules.append({
                "pattern": value,
                "allow": field == "allow",
                "line": lineno,
                # Verbatim source text of the rule (comment already removed).
                "text": content,
                "segments": segments,
                "anchored": anchored,
            })
            group_has_content = True
            continue

        if field == "crawl-delay":
            if not value:
                skipped.append(lineno)
                continue
            delay = _parse_delay(value)
            if delay is None:
                skipped.append(lineno)
                continue
            if current is None:
                skipped.append(lineno)
                continue
            current.delays.append(delay)
            group_has_content = True
            continue

        if field == "sitemap":
            if not value:
                skipped.append(lineno)
                continue
            # Sitemaps belong to the whole document, not to a group.
            sitemaps.append(value)
            continue

        # Unknown field.
        skipped.append(lineno)

    return groups, sitemaps, skipped


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _select_group(groups, agent):
    """Select a group: exact match, then longest prefix, then the '*' group.

    Returns ``(group, exact)`` where ``exact`` is True only for an identical
    User-agent match.  The group reported in the output is always the group's
    representative (its first declared identifier), independently of which
    identifier caused the match.
    """
    wanted = (agent or "").strip().casefold()

    for group in groups:
        if group.has_agent(wanted):
            return group, True

    best_group = None
    best_len = -1
    for group in groups:
        for folded, _ in group.agents:
            if not folded or folded == "*":
                continue
            if wanted.startswith(folded) and len(folded) > best_len:
                best_len = len(folded)
                best_group = group
    if best_group is not None:
        return best_group, False

    for group in groups:
        if group.has_agent("*"):
            return group, False

    return None, False


def _wildcard_group(groups):
    for group in groups:
        if group.has_agent("*"):
            return group
    return None


def _target_for_url(url):
    """Return the decoded path plus the raw query string."""
    if not isinstance(url, str):
        url = "" if url is None else str(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return "/"

    path = parts.path or "/"
    # Exactly one percent-decoding pass over the path.
    path = unquote(path)
    if parts.query:
        return path + "?" + parts.query
    return path


def _match_rule(group, target):
    """Pick the winning rule in a group, or None.

    The longest pattern wins and an equal-length Allow beats a Disallow.  Any
    remaining tie is broken by the pattern text (then by line), so the reported
    rule does not depend on the order the rules were written in.
    """
    if group is None:
        return None

    candidates = [rule for rule in group.rules
                  if _matches(rule["segments"], rule["anchored"], target)]
    if not candidates:
        return None

    best_len = max(len(rule["pattern"]) for rule in candidates)
    candidates = [rule for rule in candidates if len(rule["pattern"]) == best_len]

    allow_rules = [rule for rule in candidates if rule["allow"]]
    if allow_rules:
        candidates = allow_rules

    return min(candidates, key=lambda rule: (rule["pattern"], rule["line"]))


def process(data):
    robots_text = data.get("robots", "")
    if not isinstance(robots_text, str):
        robots_text = ""
    groups, sitemaps, skipped = _parse(robots_text)

    agent = data.get("agent", "")
    if not isinstance(agent, str):
        agent = ""

    urls = data.get("urls", [])
    if not isinstance(urls, list):
        urls = []

    group, exact = _select_group(groups, agent)

    # The matched group's Crawl-delay is the scheduling hint.  When there was
    # no exact group, the '*' group's delay applies as well.
    crawl_delay = None
    if group is not None:
        crawl_delay = group.delays[0] if group.delays else None
        if crawl_delay is None and not exact:
            fallback = _wildcard_group(groups)
            if fallback is not None and fallback.delays:
                crawl_delay = fallback.delays[0]

    decisions = []
    for url in urls:
        target = _target_for_url(url)
        rule = _match_rule(group, target)
        decisions.append({
            "url": url,
            "allowed": rule["allow"] if rule is not None else True,
            "rule": rule["text"] if rule is not None else None,
            "line": rule["line"] if rule is not None else None,
            "group": group.representative if group is not None else None,
        })

    return {
        "decisions": decisions,
        "sitemaps": sitemaps,
        "crawl_delay": crawl_delay,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8-sig") as fh:
        data = json.load(fh)

    result = process(data)
    with args.output.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
