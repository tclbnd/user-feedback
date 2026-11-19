"""Nightly full reload of the dimension tables.

The ETL rebuilds a dimension by loading a fresh copy and swapping it in
under the original name: drop the old table, create the new one with the
loaded row count, and recreate the indexes listed in ``indexes`` with
their old names.  The loader counts the rows while loading, so the new
table starts with accurate planner statistics and no separate ANALYZE is
run.
"""


def swap_reload(catalog, table, rows, indexes):
    """``indexes`` maps index name -> column for the reloaded table."""
    catalog.drop_table(table)
    catalog.create_table(table, rows)
    for name, column in sorted(indexes.items()):
        catalog.create_index(name, table, column)


def indexes_of(catalog, table):
    return {name: col for name, (t, col) in catalog.indexes.items() if t == table}
