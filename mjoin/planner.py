"""Join method choice: merge join when both inputs arrive sorted on the
join key, nested loop for tiny inputs."""
from .inputs import SortedInput
from .mergejoin import merge_join
from .nestloop import nested_loop

TINY = 8


def join(left_rows, right_rows, lkey, rkey, kind="inner", sorted_inputs=True, filters=None):
    if not sorted_inputs or (len(left_rows) <= TINY and len(right_rows) <= TINY):
        return {"method": "nested loop", "rows": nested_loop(left_rows, right_rows, lkey, rkey, kind, filters), "fetches": None}
    left, right = SortedInput(left_rows, lkey), SortedInput(right_rows, rkey)
    rows = merge_join(left, right, kind, filters)
    return {"method": "merge join", "rows": rows, "fetches": left.fetches + right.fetches}
