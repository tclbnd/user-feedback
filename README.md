# dtfn

Date and time functions of the reporting engine for `date`, `timestamp`
(without time zone) and `interval`: `extract`, `date_trunc`, date and
interval arithmetic, `make_date`, `justify_*`, interval text output and
`to_char`. Reports run against PostgreSQL and against this engine and are
compared, so values, text output and error cases must be PostgreSQL's
(DateStyle ISO, IntervalStyle postgres, English month and day names).

## Layout

- `dtfn/values.py` – parsing and text output of dates, timestamps and intervals
- `dtfn/funcs.py` – extract, date_trunc, arithmetic, make_date, justify_*
- `dtfn/tochar.py` – to_char template patterns
- `dtcall.py` – evaluates a list of calls

## Running

    python3 dtcall.py examples/report.json out.json
    python3 -m unittest discover -s tests
