#!/usr/bin/env python3
"""Redirect-chain resolver.

Usage: python3 redirect.py input.json output.json

Input JSON:
  requests: [{"id": str, "url": str}, ...]
  hops:     {"<url>": {"status": int, "location": str}, ...}
  max_hops: int

Output JSON:
  results:       [{"id", "final", "terminal", "hops", "loop_start", "downgrades"}, ...]
  stats:         {"requests", "terminal": {...}, "hops_total"}
  trace_digest:  sha256 of the compact JSON encoding of results

Each request is followed from its own URL. A URL present in "hops" yields its
recorded response; a URL absent from "hops" is the end of the chain. Only the
301/302/303/307/308 statuses redirect. A Location is resolved against the
current URL per RFC 3986 (section 5.2, strict), so relative references,
"./" and "../" segments, query-only and fragment-only references, absolute
paths and network-path references all behave as specified. A Location holding
whitespace, or one resolving to a scheme other than http/https, makes the
chain "invalid".

For loop detection each visited URL is reduced to a canonical form: scheme and
host lower-cased, default port dropped, dot segments resolved; path case,
query text and parameter order are kept. Reaching a canonical URL already
visited makes the chain "loop", and "loop_start" is that URL's index in the
canonical chain (the request URL is index 0). Taking max_hops redirects makes
the chain "limit". Results are sorted by id, and trace_digest hashes their
compact JSON encoding, so replaying a batch in any request order, or twice,
yields the same results, stats and digest.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
DEFAULT_PORTS = {"http": 80, "https": 443}

# RFC 3986, Appendix B.
_URI_RE = re.compile(
    r"^(?:([^:/?#]+):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# RFC 3986 component handling
# ---------------------------------------------------------------------------

def split_uri(uri):
    """Return (scheme, authority, path, query, fragment); absent parts are None."""
    return _URI_RE.match(uri).groups()


def unsplit_uri(scheme, authority, path, query, fragment):
    out = []
    if scheme is not None:
        out.append(scheme + ":")
    if authority is not None:
        out.append("//" + authority)
    out.append(path)
    if query is not None:
        out.append("?" + query)
    if fragment is not None:
        out.append("#" + fragment)
    return "".join(out)


def remove_dot_segments(path):
    """RFC 3986 section 5.2.4."""
    inp = path
    out = []
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
            start = 1 if inp.startswith("/") else 0
            idx = inp.find("/", start)
            if idx == -1:
                out.append(inp)
                inp = ""
            else:
                out.append(inp[:idx])
                inp = inp[idx:]
    return "".join(out)


def merge_paths(base_authority, base_path, ref_path):
    """RFC 3986 section 5.2.3."""
    if base_authority is not None and base_path == "":
        return "/" + ref_path
    idx = base_path.rfind("/")
    if idx == -1:
        return ref_path
    return base_path[: idx + 1] + ref_path


def resolve_reference(base, ref):
    """RFC 3986 section 5.2.2, strict. Returns the target URI."""
    b_scheme, b_auth, b_path, b_query, _b_frag = split_uri(base)
    r_scheme, r_auth, r_path, r_query, r_frag = split_uri(ref)

    if r_scheme is not None:
        t_scheme = r_scheme
        t_auth = r_auth
        t_path = remove_dot_segments(r_path)
        t_query = r_query
    else:
        if r_auth is not None:
            t_auth = r_auth
            t_path = remove_dot_segments(r_path)
            t_query = r_query
        else:
            if r_path == "":
                t_path = b_path
                t_query = r_query if r_query is not None else b_query
            else:
                if r_path.startswith("/"):
                    t_path = remove_dot_segments(r_path)
                else:
                    t_path = remove_dot_segments(
                        merge_paths(b_auth, b_path, r_path)
                    )
                t_query = r_query
            t_auth = b_auth
        t_scheme = b_scheme
    return unsplit_uri(t_scheme, t_auth, t_path, t_query, r_frag)


def split_authority(authority):
    """Split an authority into (userinfo, host, port); absent parts are None.

    The host may be a bracketed IPv6 literal. The port is returned as a raw
    string so that an empty port ("http://host:/x") differs from no port.
    """
    userinfo = None
    hostport = authority
    if "@" in hostport:
        userinfo, hostport = hostport.rsplit("@", 1)

    port = None
    if hostport.startswith("["):
        close = hostport.find("]")
        if close == -1:
            host = hostport
        else:
            host = hostport[: close + 1]
            rest = hostport[close + 1:]
            if rest.startswith(":"):
                port = rest[1:]
            elif rest:
                host = hostport
    elif ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host = hostport
    return userinfo, host, port


def normalize(url):
    """Canonical form of a URL, used for loop detection.

    Scheme and host are lower-cased, the default port is dropped and dot
    segments are resolved. Everything else -- path case, query text, parameter
    order, userinfo and fragment -- is preserved.
    """
    scheme, authority, path, query, fragment = split_uri(url)
    if authority is None:
        return url

    userinfo, host, port = split_authority(authority)
    host = host.lower()

    scheme_l = scheme.lower() if scheme is not None else ""
    if port is not None:
        if port == "" or (
            port.isdigit() and DEFAULT_PORTS.get(scheme_l) == int(port)
        ):
            port = None

    auth = ""
    if userinfo is not None:
        auth += userinfo + "@"
    auth += host
    if port is not None:
        auth += ":" + port

    if scheme is not None:
        scheme = scheme_l
    return unsplit_uri(
        scheme, auth, remove_dot_segments(path or ""), query, fragment
    )


def scheme_of(url):
    scheme = split_uri(url)[0]
    return scheme.lower() if scheme is not None else ""


def location_is_valid(location):
    """A Location is unusable when it is not a string or contains whitespace.

    Whitespace is never percent-encoded back for us here, so such a value is
    reported as invalid instead of being guessed at.
    """
    if not isinstance(location, str):
        return False
    return not any(ch.isspace() for ch in location)


# ---------------------------------------------------------------------------
# Chain resolution
# ---------------------------------------------------------------------------

def as_status(value):
    """Status codes are honoured as 301/302/303/307/308 only."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def resolve_request(req, hops_map, max_hops):
    current = req.get("url")
    if not isinstance(current, str):
        current = ""

    norm = normalize(current)
    seen = {norm: 0}
    chain = [norm]
    hops = []
    downgrades = 0
    loop_start = None
    terminal = "ok"

    while True:
        entry = hops_map.get(current)
        if not isinstance(entry, dict):
            break  # an absent key means this URL has no further response

        status = as_status(entry.get("status"))
        if status not in REDIRECT_STATUSES:
            break  # a non-redirect status is a terminal response

        if len(hops) >= max_hops:
            terminal = "limit"
            break

        location = entry.get("location")
        hops.append({"url": current, "status": status, "location": location})

        if not location_is_valid(location):
            terminal = "invalid"
            break

        target = resolve_reference(current, location)
        if scheme_of(target) not in ("http", "https"):
            terminal = "invalid"
            break

        if scheme_of(current) == "https" and scheme_of(target) == "http":
            downgrades += 1

        current = target
        norm = normalize(target)
        if norm in seen:
            loop_start = seen[norm]
            terminal = "loop"
            break
        seen[norm] = len(chain)
        chain.append(norm)

    return {
        "id": req.get("id"),
        "final": current,
        "terminal": terminal,
        "hops": hops,
        "loop_start": loop_start,
        "downgrades": downgrades,
    }


def result_sort_key(result):
    """Order results by id (lexicographic); ties broken by the entry itself."""
    return (str(result["id"]), json.dumps(result, sort_keys=True))


def process(data):
    requests = data.get("requests") or []
    hops_map = data.get("hops") or {}
    raw_limit = data.get("max_hops")
    if raw_limit is None:
        max_hops = float("inf")
    else:
        try:
            max_hops = max(0, int(raw_limit))
        except (TypeError, ValueError):
            max_hops = float("inf")

    results = [resolve_request(req, hops_map, max_hops) for req in requests]
    results.sort(key=result_sort_key)

    counts = {}
    for result in results:
        counts[result["terminal"]] = counts.get(result["terminal"], 0) + 1

    stats = {
        "requests": len(results),
        "terminal": dict(sorted(counts.items())),
        "hops_total": sum(len(r["hops"]) for r in results),
    }

    compact = json.dumps(results, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()
    return {"results": results, "stats": stats, "trace_digest": digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve redirect chains.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("redirect.py: cannot read %s: %s" % (args.input, exc), file=sys.stderr)
        return 1

    text = json.dumps(process(data), ensure_ascii=False, indent=2) + "\n"
    try:
        args.output.write_text(text, encoding="utf-8")
    except OSError as exc:
        print("redirect.py: cannot write %s: %s" % (args.output, exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
