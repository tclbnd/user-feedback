"""Follow redirect chains described by a local hops table.

Usage: python3 redirect.py input.json output.json

Input: ``requests`` (``id``/``url``), ``hops`` (URL -> ``status``/``location``;
a missing key means "no further response", i.e. the endpoint) and ``max_hops``.

Location values are resolved against the current URL with RFC 3986
relative-reference rules (dot segments, query-only, fragment-only, network-path
and absolute-path references).  Cycles are detected on a normalised form of the
URL; each request ends up in one of the states ``ok``/``loop``/``limit``/
``invalid``.
"""

import argparse
import hashlib
import json
import re
from collections import namedtuple
from pathlib import Path
from urllib.parse import urljoin

REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
DEFAULT_PORT = {"http": 80, "https": 443}
HTTP_SCHEMES = frozenset(("http", "https"))

# RFC 3986 appendix B: scheme, authority, path, query, fragment.
URI_RE = re.compile(r"^(?:([^:/?#]+):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$", re.S)

URI = namedtuple("URI", "scheme authority path query fragment")


def parse_uri(value):
    """Split a URI reference into components (RFC 3986 appendix B)."""
    if not isinstance(value, str):
        value = ""
    match = URI_RE.match(value)
    if match is None:  # pragma: no cover - the pattern matches any string
        return URI(None, None, value, None, None)
    return URI(*match.groups(default=None))


def _split_netloc(netloc):
    """Split an authority into (userinfo, host, port); IPv6 literals stay whole."""
    userinfo = ""
    rest = netloc
    if "@" in rest:
        userinfo, _, rest = rest.rpartition("@")
    if rest.startswith("["):
        end = rest.find("]")
        if end != -1:
            host = rest[: end + 1]
            tail = rest[end + 1:]
            port = tail[1:] if tail.startswith(":") else None
            return userinfo, host, port
    if ":" in rest:
        host, _, port = rest.rpartition(":")
        return userinfo, host, port
    return userinfo, rest, None


def remove_dot_segments(path):
    """RFC 3986 section 5.2.4 dot-segment removal."""
    out = []
    inp = path
    while inp:
        if inp.startswith("../"):
            inp = inp[3:]
        elif inp.startswith("./"):
            inp = inp[2:]
        elif inp.startswith("/./"):
            inp = "/" + inp[3:]
        elif inp == "/.":
            inp = "/"
        elif inp.startswith("/../"):
            inp = "/" + inp[4:]
            if out:
                out.pop()
        elif inp == "/..":
            inp = "/"
            if out:
                out.pop()
        elif inp in (".", ".."):
            inp = ""
        else:
            idx = inp.find("/", 1 if inp.startswith("/") else 0)
            if idx == -1:
                out.append(inp)
                inp = ""
            else:
                out.append(inp[:idx])
                inp = inp[idx:]
    return "".join(out)


def normalize_url(url):
    """Canonical form used for cycle detection.

    Lower-cases scheme and host, drops a default port, removes dot segments and
    strips the fragment; the rest of the path and the query are preserved
    verbatim (case and parameter order included).
    """
    if not isinstance(url, str):
        return ""
    uri = parse_uri(url)
    scheme = (uri.scheme or "").lower()
    netloc = uri.authority
    if netloc is None:
        normalized = scheme + ":" if scheme else ""
        normalized += remove_dot_segments(uri.path)
        if uri.query is not None:
            normalized += "?" + uri.query
        return normalized
    userinfo, host, port = _split_netloc(netloc)
    host = host.lower()
    number = None
    if port:
        try:
            number = int(port)
        except (TypeError, ValueError):
            number = None
    if number is not None and DEFAULT_PORT.get(scheme) == number:
        port = None
    normalized = scheme + ":" if scheme else ""
    normalized += "//"
    if userinfo:
        normalized += userinfo + "@"
    normalized += host
    if port:
        normalized += ":" + port
    normalized += remove_dot_segments(uri.path)
    if uri.query is not None:
        normalized += "?" + uri.query
    return normalized


def scheme_of(url):
    if not isinstance(url, str):
        return ""
    return (parse_uri(url).scheme or "").lower()


def resolve_location(base, location):
    """Resolve a Location value against ``base``; ``None`` when invalid.

    ``urljoin`` implements the RFC 3986 relative-reference resolution
    algorithm, so dot segments, query-only references, fragment-only
    references, network-path references and absolute paths all behave as
    specified.
    """
    if not isinstance(location, str):
        return None
    # A Location carrying whitespace is not a usable URI reference.
    if any(ch.isspace() for ch in location):
        return None
    try:
        resolved = urljoin(base, location)
        scheme = (parse_uri(resolved).scheme or "").lower()
    except ValueError:
        return None
    if scheme not in HTTP_SCHEMES:
        return None
    return resolved


def status_number(status):
    if isinstance(status, bool) or status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def process_request(request, hops_map, max_hops):
    if not isinstance(request, dict):
        request = {}
    url = request.get("url")
    hops = []
    normalized_chain = [normalize_url(url)]
    seen = {normalized_chain[0]: 0}
    final = url
    terminal = "ok"
    loop_start = None
    downgrades = 0

    while True:
        if len(hops) >= max_hops:
            # The hop count has reached ``max_hops``: the budget is exhausted.
            terminal = "limit"
            final = url
            break

        entry = hops_map.get(url)
        if not isinstance(entry, dict):
            # No recorded response for this URL: it is the endpoint.
            terminal = "ok"
            final = url
            break

        status = entry.get("status")
        location = entry.get("location")
        hops.append({"url": url, "status": status, "location": location})

        if status_number(status) not in REDIRECT_STATUSES:
            # Not a redirect status: this URL is the endpoint.
            terminal = "ok"
            final = url
            break

        resolved = resolve_location(url, location)
        if resolved is None:
            terminal = "invalid"
            final = url
            break

        if scheme_of(url) == "https" and scheme_of(resolved) == "http":
            downgrades += 1

        canonical = normalize_url(resolved)
        if canonical in seen:
            # The normalised target was visited already: a cycle.
            terminal = "loop"
            loop_start = seen[canonical]
            final = resolved
            break

        seen[canonical] = len(normalized_chain)
        normalized_chain.append(canonical)
        final = resolved
        url = resolved

    return {
        "id": request.get("id"),
        "final": final,
        "terminal": terminal,
        "hops": hops,
        "loop_start": loop_start,
        "downgrades": downgrades,
    }


def process(data):
    hops_map = data.get("hops") or {}
    if not isinstance(hops_map, dict):
        hops_map = {}
    max_hops = _as_int(data.get("max_hops"), None)
    if max_hops is None or max_hops < 0:
        # No usable budget supplied: consume the whole table at most, which
        # still terminates because a repeated URL is reported as a loop.
        max_hops = len(hops_map) + 1

    results = [process_request(req, hops_map, max_hops) for req in data.get("requests") or []]
    # Dictionary order on the id; the serialised item breaks ties so that
    # duplicate ids keep the same order however the requests were supplied.
    results.sort(key=lambda item: (_id_key(item["id"]), _result_key(item)))

    by_terminal = {}
    hops_total = 0
    for item in results:
        by_terminal[item["terminal"]] = by_terminal.get(item["terminal"], 0) + 1
        hops_total += len(item["hops"])

    stats = {
        "requests": len(results),
        "by_terminal": {key: by_terminal[key] for key in sorted(by_terminal)},
        "hops_total": hops_total,
    }
    digest = hashlib.sha256(
        json.dumps(results, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"results": results, "stats": stats, "trace_digest": digest}


def _as_int(value, default):
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _id_key(value):
    """Order ids dictionary-style, keeping numeric ids in numeric order."""
    if isinstance(value, bool) or value is None:
        return (0, "")
    if isinstance(value, (int, float)):
        return (1, value)
    return (2, str(value))


def _result_key(item):
    return json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = process(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
