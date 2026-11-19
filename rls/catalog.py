"""Tables, views, row-level security policies and user functions.

Every table carries a ``tenant`` column and the policy ``tenant = current
tenant``.  Views run with the privileges and policies of the *caller*
(security invoker).  A function marked ``leakproof`` reveals nothing about
its arguments other than its return value; other functions may have side
effects (logging, raising errors with the value in the message).
"""


class QueryError(Exception):
    pass


class Catalog:
    def __init__(self, tables, views=None):
        self.tables = tables
        self.views = views or {}
        self.expanded_views = {}    # view name -> expanded plan, filled by the planner
        self.leak_log = []
        self.functions = {
            "eq": (True, lambda a, b: a == b),
            "starts_with": (True, lambda a, b: isinstance(a, str) and a.startswith(b)),
            "audit_log": (False, self._audit),
            "fail_if": (False, self._fail_if),
        }

    def _audit(self, *args):
        self.leak_log.append(list(args))
        return True

    def _fail_if(self, value, bad):
        if value == bad:
            raise QueryError(f"invalid input value: {value!r}")
        return True

    def policy(self, table, tenant):
        return {"cmp": ["tenant", "=", tenant]}

    def leakproof(self, name):
        return self.functions[name][0]
