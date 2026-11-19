"""COPY ... FROM ... WITH (FORMAT csv, ...) options, PostgreSQL names and defaults.

delimiter (','), quote ('"'), escape (same as quote), null (''),
header (false), force_null ([cols]), force_not_null ([cols]).
"""

DEFAULTS = {"delimiter": ",", "quote": '"', "escape": None, "null": "", "header": False,
            "force_null": [], "force_not_null": []}


def resolve(options):
    opts = dict(DEFAULTS)
    opts.update(options or {})
    if opts["escape"] is None:
        opts["escape"] = opts["quote"]
    for name in ("delimiter", "quote", "escape"):
        if len(opts[name]) != 1:
            raise ValueError(f"COPY {name} must be a single one-byte character")
    return opts
