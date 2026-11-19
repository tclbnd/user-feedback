"""Key-frequency statistics shown in the schema explorer."""
from collections import Counter


def key_frequencies(docs):
    counts = Counter()

    def walk(v, prefix):
        if isinstance(v, dict):
            for k, x in v.items():
                counts[prefix + "." + k] += 1
                walk(x, prefix + "." + k)
        elif isinstance(v, list):
            for x in v:
                walk(x, prefix + "[*]")

    for d in docs.values():
        walk(d, "$")
    return dict(counts.most_common())
