"""COPY ... FROM ... WITH (FORMAT csv, ...) options, PostgreSQL names and defaults.

delimiter (','), quote ('"'), escape (same as quote), null (''),
header (false), force_null ([cols]), force_not_null ([cols]).

``resolve`` applies the same checks the server runs in
``ProcessCopyOptions`` and raises :class:`~csvcopy.loader.CopyError` with
the server's message text when an option is invalid.
"""

DEFAULTS = {"delimiter": ",", "quote": '"', "escape": None, "null": "",
            "header": False, "force_null": [], "force_not_null": []}


class CopyError(Exception):
    """Raised with the message text the server would send to the client."""


# Error messages are the exact strings used by ProcessCopyOptions.
_MULTIBYTE = {
    "delimiter": "COPY delimiter must be a single one-byte character",
    "quote": "COPY quote must be a single one-byte character",
    "escape": "COPY escape must be a single one-byte character",
}
_CONFLICT = {
    "quote": 'COPY quote parameter cannot be the same as the delimiter',
    "escape": 'COPY escape parameter cannot be the same as the delimiter',
}


def _check_one_byte(name, value):
    # The server measures client_encoding bytes; for the UTF-8 input used
    # here, one character must encode to exactly one byte.
    if not isinstance(value, str) or len(value) != 1 or len(value.encode("utf-8")) != 1:
        raise CopyError(_MULTIBYTE[name])


def resolve(options=None, columns=None):
    """Merge *options* over the CSV defaults and validate them.

    *columns* is the list of target column names; when given, the
    FORCE_NULL / FORCE_NOT_NULL lists are checked against it the way the
    server checks them against the table.
    """
    opts = dict(DEFAULTS)
    opts.update(options or {})
    if opts["escape"] is None:
        opts["escape"] = opts["quote"]

    for name in ("delimiter", "quote", "escape"):
        _check_one_byte(name, opts[name])
    if opts["quote"] == opts["delimiter"]:
        raise CopyError(_CONFLICT["quote"])
    if opts["escape"] == opts["delimiter"]:
        raise CopyError(_CONFLICT["escape"])

    null = opts["null"]
    if not isinstance(null, str):
        raise CopyError("COPY null representation must be a string")
    if "\n" in null or "\r" in null:
        raise CopyError("CSV null representation must not contain carriage return or newline")
    if opts["quote"] in null:
        raise CopyError("CSV quote character must not appear in the NULL specification")

    if not isinstance(opts["header"], bool):
        raise CopyError("COPY header option must be a boolean")

    force_null = list(opts["force_null"])
    force_not_null = list(opts["force_not_null"])
    opts["force_null"] = force_null
    opts["force_not_null"] = force_not_null

    for label, names in (("force_null", force_null), ("force_not_null", force_not_null)):
        seen = set()
        for name in names:
            if name in seen:
                raise CopyError(f'column "{name}" appears more than once in COPY {label.upper()} option')
            seen.add(name)
    if columns is not None:
        valid = set(columns)
        for name in force_null + force_not_null:
            if name not in valid:
                raise CopyError(f'column "{name}" referenced in COPY option does not exist')
    overlap = set(force_null) & set(force_not_null)
    if overlap:
        name = sorted(overlap)[0]
        raise CopyError(
            f'cannot use FORCE_NULL and FORCE_NOT_NULL options together on column "{name}"'
        )
    return opts

