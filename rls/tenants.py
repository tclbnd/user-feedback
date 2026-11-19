"""Tenant onboarding helpers used by the admin console."""


def tenant_summary(catalog):
    out = {}
    for table, rows in catalog.tables.items():
        for r in rows:
            out.setdefault(r["tenant"], {}).setdefault(table, 0)
            out[r["tenant"]][table] += 1
    return dict(sorted(out.items()))


def validate_rows(catalog):
    bad = []
    for table, rows in catalog.tables.items():
        for r in rows:
            if not r.get("tenant"):
                bad.append((table, r.get("id")))
    return bad
