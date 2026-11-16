#!/usr/bin/env python3
"""Canonical URL normalizer.

Entry point:  python3 urlnorm.py input.json output.json
Standard library only.
"""
import argparse
import json
from encodings import idna
from pathlib import Path

# Reporting order for the normalization actions that fired.
RULE_ORDER = ["case", "port", "dotseg", "pct", "idn",
              "userinfo", "params", "fragment"]

DEFAULT_PORTS = {"http": 80, "https": 443}
_HEX = set("0123456789abcdefABCDEF")
_UNRESERVED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  b"abcdefghijklmnopqrstuvwxyz"
                  b"0123456789-_.~")


# --------------------------------------------------------------------------
# Percent encoding helpers (path normalization)
# --------------------------------------------------------------------------
def decode_once(text):
    """Percent-decode a string exactly once.

    Valid percent escapes become their byte; invalid escapes are kept
    literally. The result is a UTF-8 byte string.
    """
    out = bytearray()
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "%" and i + 2 < n \
                and text[i + 1] in _HEX and text[i + 2] in _HEX:
            out.append(int(text[i + 1:i + 3], 16))
            i += 3
        else:
            out.extend(ch.encode("utf-8"))
            i += 1
    return bytes(out)


def reencode(raw):
    """Unreserved bytes stay literal; every other byte gets uppercase hex."""
    parts = []
    for byte in raw:
        if byte in _UNRESERVED:
            parts.append(chr(byte))
        else:
            parts.append("%%%02X" % byte)
    return "".join(parts)


def clean_segment(seg):
    return reencode(decode_once(seg))


def remove_dot_segments(decoded_segments, absolute):
    """Dot-segment resolution over already-decoded segment strings.

    Never adds or removes a trailing slash: empty strings produced by
    split("/") are left untouched.
    """
    stack = []
    for seg in decoded_segments:
        if seg == ".":
            continue
        if seg == "..":
            if stack and stack[-1] not in ("", ".."):
                stack.pop()
            elif not absolute:
                stack.append("..")
            continue
        stack.append(seg)
    return stack


def normalize_path(path):
    """Return (canonical_path, dotseg_changed, pct_changed)."""
    if path == "":
        return "/", False, False

    raw_segments = path.split("/")
    # Percent encoding is judged against the raw segment text so that
    # dot-segment removal alone never trips the "pct" rule.
    pct_changed = any(clean_segment(seg) != seg for seg in raw_segments)
    decoded = [decode_once(seg).decode("utf-8", "surrogatepass")
               for seg in raw_segments]
    resolved = remove_dot_segments(decoded, path.startswith("/"))
    dotseg_changed = resolved != decoded

    cleaned = [reencode(seg.encode("utf-8", "surrogatepass"))
               for seg in resolved]
    return "/".join(cleaned), dotseg_changed, pct_changed


# --------------------------------------------------------------------------
# Authority (netloc) normalization
# --------------------------------------------------------------------------
def normalize_authority(netloc):
    """Return (host, port, rules, userinfo_stripped_flag).

    host is the canonical host text (ASCII host or bracketed IPv6 literal).
    """
    rules = set()
    userinfo_stripped = False
    hostport = netloc
    if "@" in netloc:
        _, hostport = netloc.rsplit("@", 1)
        userinfo_stripped = True
        rules.add("userinfo")

    port = None
    if hostport.startswith("["):
        # IPv6 literal: keep brackets and the zone identifier verbatim.
        close = hostport.find("]")
        literal = hostport[1:close]
        tail = hostport[close + 1:]
        if tail.startswith(":") and tail[1:]:
            port = int(tail[1:])
        if "%" in literal:
            v6, zone = literal.split("%", 1)
            host = "[" + v6.lower() + "%" + zone + "]"
        else:
            host = "[" + literal.lower() + "]"
        if host != "[" + literal + "]":
            rules.add("case")
        return host, port, rules, userinfo_stripped

    if ":" in hostport:
        host_raw, port_s = hostport.rsplit(":", 1)
        if port_s:
            port = int(port_s)
    else:
        host_raw = hostport

    host_lower = host_raw.lower()
    if host_lower != host_raw:
        rules.add("case")
    # IDN: convert each internationalized label to Punycode ASCII.
    # encodings.idna works label-wise, so split the host first.
    try:
        labels = [idna.ToASCII(label).decode("ascii")
                  for label in host_lower.split(".")]
        host_ascii = ".".join(labels)
    except (UnicodeError, UnicodeDecodeError):
        host_ascii = host_lower
    if host_ascii != host_lower:
        rules.add("idn")
    return host_ascii, port, rules, userinfo_stripped


# --------------------------------------------------------------------------
# Query handling
# --------------------------------------------------------------------------
def split_query(query):
    """Split a raw query into (name, value) pairs without decoding.

    A value of None means the parameter had no '=' at all.
    """
    pairs = []
    if query == "":
        return pairs
    for part in query.split("&"):
        if part == "":
            continue
        if "=" in part:
            name, value = part.split("=", 1)
        else:
            name, value = part, None
        pairs.append((name, value))
    return pairs


def serialize_pairs(pairs):
    return "&".join(name if value is None else name + "=" + value
                    for name, value in pairs)


def apply_policy(pairs, policy):
    """Filter/dedup query pairs. Returns (pairs, stripped_counts, triggered)."""
    mode = policy.get("params_mode")
    name_set = set(policy.get("params", []))
    stripped = {}
    kept = []
    for name, value in pairs:
        if mode == "deny":
            drop = name in name_set
        elif mode == "allow":
            drop = name not in name_set
        else:
            drop = False
        if drop:
            stripped[name] = stripped.get(name, 0) + 1
        else:
            kept.append((name, value))

    triggered = bool(stripped)

    if policy.get("dedup_values"):
        seen = {}
        deduped = []
        for name, value in kept:
            values = seen.setdefault(name, [])
            if value in values:
                triggered = True
                continue
            values.append(value)
            deduped.append((name, value))
        kept = deduped

    # Merge by parameter name (stable sort keeps same-name relative order).
    kept.sort(key=lambda item: item[0])
    return kept, stripped, triggered


# --------------------------------------------------------------------------
# Full URL normalization
# --------------------------------------------------------------------------
def normalize(url, policy):
    raw = url.strip()
    rules = set()

    # scheme
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
    else:
        scheme, rest = raw, ""
    scheme_l = scheme.lower()
    if scheme_l != scheme:
        rules.add("case")

    # fragment
    if "#" in rest:
        rest = rest.split("#", 1)[0]
        rules.add("fragment")

    # query
    if "?" in rest:
        authority_path, query = rest.split("?", 1)
    else:
        authority_path, query = rest, ""

    # authority / path
    if "/" in authority_path:
        netloc, path_tail = authority_path.split("/", 1)
        path = "/" + path_tail
    else:
        netloc, path = authority_path, ""

    host, port, auth_rules, had_userinfo = normalize_authority(netloc)
    rules |= auth_rules

    if port is not None and DEFAULT_PORTS.get(scheme_l) == port:
        port = None
        rules.add("port")

    canon_path, dotseg_changed, pct_changed = normalize_path(path)
    if dotseg_changed:
        rules.add("dotseg")
    if pct_changed:
        rules.add("pct")

    pairs, stripped, params_triggered = apply_policy(split_query(query), policy)
    if params_triggered:
        rules.add("params")
    canon_query = serialize_pairs(pairs)

    netloc_canon = host + (":" + str(port) if port is not None else "")
    result = scheme_l + "://" + netloc_canon + canon_path
    if canon_query:
        result += "?" + canon_query

    return result, [r for r in RULE_ORDER if r in rules], stripped, had_userinfo


def process(data):
    policy = data.get("policy", {})
    urls = data.get("urls", [])

    canonical = {}
    rules_applied = {}
    stripped_total = {}
    userinfo_stripped = 0

    for url in urls:
        canon, hit_rules, stripped, had_ui = normalize(url, policy)
        canonical[url] = canon
        rules_applied[url] = hit_rules
        if had_ui:
            userinfo_stripped += 1
        for name, count in stripped.items():
            stripped_total[name] = stripped_total.get(name, 0) + count

    unique = sorted(set(canonical.values()))
    stats = {
        "total": len(urls),
        "unique": len(unique),
        "stripped": {key: stripped_total[key] for key in sorted(stripped_total)},
        "userinfo_stripped": userinfo_stripped,
    }
    return {
        "canonical": canonical,
        "rules_applied": rules_applied,
        "unique": unique,
        "dropped": len(urls) - len(unique),
        "stats": stats,
    }


def main():
    ap = argparse.ArgumentParser(description="Canonical URL normalizer")
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.write_text(
        json.dumps(process(data), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

