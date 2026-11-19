# ftsearch

The inverted index behind site search. New and edited articles go to an
in-memory write buffer that is periodically flushed into immutable
segments; a background job merges segments.

## Layout

- `ftsearch/tokenizer.py` – lowercase word tokens with positions
- `ftsearch/segment.py` – write buffer / frozen segment, posting lists with positions
- `ftsearch/index.py` – the index: add, update, delete, flush, word search and phrase search
- `ftsearch/merge.py` – segment merging: full merge, size-tiered merge and the background merge used during the day
- `ftsearch/highlight.py` – result snippets
- `run.py` – replays a list of operations and queries

## Running

    python3 run.py examples/articles.json out.json
    python3 -m unittest discover -s tests

`search` returns articles containing all query words; `phrase` returns
articles where the words appear next to each other in the given order.
Results are sorted article ids. Stats report the number of frozen segments,
the total number of (term, article) postings and the number of merges.
Besides `merge` (merge everything), the operations file accepts
`merge_tier` and the pair `merge_begin` / `merge_commit`, which run the
size-tiered merge in the background while other operations go on.

## Deletes and updates

Every write to an article (add, update, delete) bumps its version, and
each posting records the version it was written with. A posting is visible
only while it is the article's latest version, so deletes and edits take
effect immediately, even for articles already frozen into segments. Merges
keep only the newest version of each article and re-validate postings at
commit time, so a delete or edit that happens while a background merge is
running is never resurrected, and merged segments carry no postings for
deleted content.
