README: python3 redirect.py input.json output.json

Resolves every request's redirect chain against the `hops` table using only
the standard library, and writes:

  results       one entry per request, sorted by id:
                {id, final, terminal, hops[{url,status,location}],
                 loop_start, downgrades}
  stats         {requests, terminal: {ok|loop|limit|invalid: n}, hops_total}
  trace_digest  SHA-256 of the compact JSON encoding of `results`

`terminal` is one of `ok` (no further response), `loop` (a canonical URL
repeats), `limit` (max_hops redirects taken) or `invalid` (Location holds
whitespace or resolves to a non-http/https scheme). Location resolution follows
RFC 3986 section 5.2, so relative paths, `./`/`../`, `?query`-only, `#frag`-only,
absolute paths and `//host/path` network-path references all work. Loop
detection compares canonical URLs (scheme/host lower-cased, default port
dropped, dot segments resolved; everything else preserved) and `loop_start` is
the repeat's index in that canonical chain, with the request URL at index 0.
`downgrades` counts https -> http redirects.

The output is order-independent: replaying the same requests shuffled, or
rerunning the batch, gives identical results, stats and digest.
