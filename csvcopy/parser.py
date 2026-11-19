"""Splitting CSV input into records and fields, PostgreSQL COPY semantics.

The parser is a character-by-character state machine following
``CopyReadLineCSV`` in src/backend/commands/copy.c:

* a quote character only opens a quoted field when it is the first
  character of the field; quotes appearing later in an unquoted field
  are ordinary data;
* inside a quoted field the escape character (QUOTE by default) escapes
  QUOTE, ESCAPE and itself; outside quoted fields ESCAPE is a plain
  character;
* delimiters and record terminators inside quotes are data;
* CR, LF and CRLF all terminate records outside quotes; a lone CR inside
  an unquoted field is an error ("unquoted carriage return found in
  data"), while any byte inside a quoted field, including newlines, is
  kept verbatim;
* a quoted field that reaches EOF without a closing quote is an error
  ("unterminated CSV quoted field").

Each field is returned as ``(text, quoted)`` so the loader can apply the
NULL rules, which depend on whether the field was quoted.  Every record
also carries the 1-based number of the *physical* input line on which it
ends, which is what PostgreSQL reports in its CONTEXT messages.
"""

from .options import CopyError


class CSVParseError(CopyError):
    """Raised with the exact message text COPY would emit."""


def parse(data, opts):
    """Yield ``(fields, end_line)`` for every record in *data*.

    *fields* is a list of ``(text, quoted)`` tuples.  A completely empty
    input produces no records; a trailing newline does not create an
    extra blank record, but a blank line in the middle of the file is a
    one-field record containing the empty string, exactly as in COPY.
    """
    delimiter = opts["delimiter"]
    quote = opts["quote"]
    escape = opts["escape"]

    fields = []
    buf = []
    in_quote = False          # inside a quoted field
    at_field_start = True     # next byte is the first byte of a field
    quoted = False            # the current field was quoted at any point
    line_no = 1
    n = len(data)
    i = 0
    while i < n:
        c = data[i]

        if not in_quote and c == quote and at_field_start:
            # Opening quote of a quoted field.  When ESCAPE differs from
            # QUOTE, COPY treats a following ESCAPE+QUOTE as an escaped
            # quote rather than an immediately closed field; when they are
            # equal (the default) the next quote always closes the field.
            in_quote = True
            quoted = True
            at_field_start = False
            if escape != quote and i + 1 < n and data[i + 1] == escape:
                in_quote = False
                buf.append(quote)
                i += 2
            else:
                i += 1
            continue

        if in_quote:
            if c == quote:
                if escape == quote:
                    # Doubled quote ("") is an escaped quote; a single
                    # quote followed by anything else (including EOF)
                    # closes the field -- or leaves it open at EOF, which
                    # the check after the loop turns into an error.
                    if i + 1 < n and data[i + 1] == quote:
                        buf.append(quote)
                        i += 2
                        continue
                    in_quote = False
                    i += 1
                    continue
                # ESCAPE is distinct: a quote always closes the field.
                in_quote = False
                i += 1
                continue
            if c == escape:
                # ESCAPE only works inside a quoted field and quotes the
                # following byte.  With no byte left to escape the field
                # stays open, so the post-loop check reports it as
                # unterminated, exactly like the server.
                if i + 1 < n:
                    buf.append(data[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if c == "\r":
                # Newlines inside a quoted field are part of the data.
                buf.append(c)
                line_no += 1
                i += 1
                continue
            if c == "\n":
                buf.append(c)
                line_no += 1
                i += 1
                continue
            buf.append(c)
            i += 1
            continue

        # Unquoted state.
        if c == "\r":
            if i + 1 < n and data[i + 1] == "\n":
                line_no += 1
                i += 2
            else:
                raise CSVParseError("unquoted carriage return found in data")
            fields.append(("".join(buf), quoted))
            yield fields, line_no
            fields = []
            buf = []
            at_field_start = True
            quoted = False
            continue
        if c == "\n":
            line_no += 1
            i += 1
            fields.append(("".join(buf), quoted))
            yield fields, line_no
            fields = []
            buf = []
            at_field_start = True
            quoted = False
            continue
        if c == delimiter:
            fields.append(("".join(buf), quoted))
            buf = []
            at_field_start = True
            quoted = False
            i += 1
            continue
        # A quote anywhere but at the field start is literal data, as is
        # an escape character outside a quoted field.
        buf.append(c)
        at_field_start = False
        i += 1

    if in_quote:
        raise CSVParseError("unterminated CSV quoted field")

    # Data after the last record terminator is itself a record; input
    # ending exactly on a terminator has already yielded the last record.
    if fields or buf or at_field_start is False:
        fields.append(("".join(buf), quoted))
        yield fields, line_no


def records(data, opts):
    """Yield lists of ``(text, quoted)`` fields, one per input record."""
    for fields, _end_line in parse(data, opts):
        yield fields
