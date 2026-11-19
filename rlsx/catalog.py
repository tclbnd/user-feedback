"""Tables, roles and row-level security policies (PostgreSQL model).

A policy: {"name", "table", "command": "all"|"select"|"insert"|"update"|"delete",
           "kind": "permissive"|"restrictive", "roles": ["public"] or role names,
           "using": expr or null, "check": expr or null}
Tables: {"owner": role, "rls": bool, "force": bool, "rows": [...]}.
Roles: {"name": {"members_of": [group roles], "bypassrls": bool, "superuser": bool}}.
"""


class Catalog:
    def __init__(self, spec):
        self.tables = spec["tables"]
        self.roles = spec.get("roles", {})
        self.policies = spec.get("policies", [])

    def role_info(self, role):
        return self.roles.get(role, {})

    def memberships(self, role):
        return {role} | set(self.role_info(role).get("members_of", []))

    def policies_for(self, table, command, role):
        mine = self.memberships(role) | {"public"}
        out = []
        for p in self.policies:
            if p["table"] != table:
                continue
            if p.get("command", "all") not in ("all", command):
                continue
            if not mine & set(p.get("roles", ["public"])):
                continue
            out.append(p)
        return out
