# ranges

Range types for the room-booking engine. Bookings are stored in
PostgreSQL range columns (int4range for seat numbers, numrange for price
bands, daterange for stays) and the availability checks run in this
engine; both sides must agree on every value, so input, canonical form,
output text, the accessor functions and every operator here must behave
exactly like PostgreSQL's, including empty and unbounded ranges and the
operations PostgreSQL rejects.

## Layout

- `ranges/rangetype.py` – range values, input and output
- `ranges/operators.py` – functions and operators
- `rangecall.py` – evaluates a list of calls

## Running

    python3 rangecall.py examples/bookings.json out.json
    python3 -m unittest discover -s tests
