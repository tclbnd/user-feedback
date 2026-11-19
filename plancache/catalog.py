"""System catalog: tables, indexes and planner statistics."""


class CatalogError(Exception):
    pass


class Table:
    def __init__(self, name, rows):
        self.name = name
        self.rows = rows            # actual row count, changes with DML
        self.stats_rows = rows      # what the planner believes, set by ANALYZE (and by a bulk load)
        self.stats_version = 1


class Catalog:
    def __init__(self):
        self.tables = {}
        self.indexes = {}           # name -> (table, column)
        self.ddl_version = 1

    def create_table(self, name, rows=0):
        if name in self.tables:
            raise CatalogError(f"relation {name} already exists")
        self.tables[name] = Table(name, rows)
        self.ddl_version += 1

    def drop_table(self, name):
        if name not in self.tables:
            raise CatalogError(f"relation {name} does not exist")
        del self.tables[name]
        for idx in [i for i, (t, _) in self.indexes.items() if t == name]:
            del self.indexes[idx]
        self.ddl_version += 1

    def insert_rows(self, name, n):
        self.tables[name].rows += n

    def analyze(self, name):
        t = self.tables[name]
        t.stats_rows = t.rows
        t.stats_version += 1

    def create_index(self, name, table, column):
        if table not in self.tables:
            raise CatalogError(f"relation {table} does not exist")
        if name in self.indexes:
            raise CatalogError(f"index {name} exists")
        self.indexes[name] = (table, column)
        self.ddl_version += 1

    def drop_index(self, name):
        if name not in self.indexes:
            raise CatalogError(f"index {name} does not exist")
        del self.indexes[name]
        self.ddl_version += 1

    def index_on(self, table, column):
        for name, (t, c) in sorted(self.indexes.items()):
            if t == table and c == column:
                return name
        return None
