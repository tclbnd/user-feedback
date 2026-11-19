# joinexec

Join operators of the executor. The planner uses a merge join when both
inputs arrive sorted on the join key (sort nodes and ordered index scans put
NULL keys last), and a nested loop for tiny inputs.

## Layout

- `mjoin/inputs.py` – sorted inputs with mark/restore and fetch counting
- `mjoin/mergejoin.py` – merge join (inner, left, right, full, semi, anti)
- `mjoin/nestloop.py` – nested loop join
- `mjoin/planner.py` – picks the join method
- `mjoin/explain.py` – EXPLAIN ANALYZE text for the join node
- `runjoin.py` – runs one join from a JSON job (`--nested-loop` forces the nested loop)

## Running

    python3 runjoin.py examples/orders.json out.json
    python3 -m unittest discover -s tests

Join keys follow SQL: a NULL key never equals anything, including another
NULL. Semi join returns each left row that has at least one match, anti
join each left row that has none. The output lists `(left id, right id)`
pairs sorted for comparison, the join method and the number of input rows
fetched.
