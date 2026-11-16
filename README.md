Usage: python3 redirect.py input.json output.json

Follows the redirect chains in `input.json` using only the standard library and
the local `hops` table (no network access):

- `requests`: list of `{"id": ..., "url": ...}`.
- `hops`: map from URL to `{"status": ..., "location": ...}`; a URL with no key
  is an endpoint.
- `max_hops`: hop budget per request.

Each `Location` is resolved against the current URL with RFC 3986 relative
references (dot segments, `?query`-only, `#frag`-only, `//host/path` and
absolute paths). A `Location` containing whitespace, or one that resolves to a
non-http(s) scheme, makes the chain `invalid`. Only 301/302/303/307/308 are
followed.

Output `results` are sorted by id (`id`, `final`, `terminal`, `hops`,
`loop_start`, `downgrades`); `stats` holds exactly `requests`, `by_terminal` and
`hops_total`; `trace_digest` is the SHA-256 of the compact `results` JSON, so it
is stable across request orderings and repeated runs.

