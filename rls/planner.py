"""Rewrites a query plan for a session: expands views and adds the
row-level security quals.

The reporting views are large and the application server keeps one
catalog for all the sessions it serves, so a view is expanded once and
the expanded plan is reused (``catalog.expanded_views``).
"""


def rewrite(plan, catalog, tenant):
    return {"secure": _expand(plan, catalog, tenant), "tenant": tenant}


def _expand(node, catalog, tenant, as_owner=False):
    if "view" in node:
        # views are expanded inline; their body was checked when the view was created
        name = node["view"]
        if name not in catalog.expanded_views:
            catalog.expanded_views[name] = _expand(catalog.views[name], catalog, tenant, as_owner=True)
        return catalog.expanded_views[name]
    if "filter" in node:
        return {"filter": _expand_pred(node["filter"], catalog, tenant),
                "input": _expand(node["input"], catalog, tenant, as_owner)}
    if "scan" in node:
        return {"scan": node["scan"], "rls": None if as_owner else catalog.policy(node["scan"], tenant)}
    raise ValueError(f"bad plan node {node}")


def _expand_pred(pred, catalog, tenant):
    if "and" in pred:
        return {"and": [_expand_pred(p, catalog, tenant) for p in pred["and"]]}
    if "exists" in pred:
        sub = pred["exists"]
        return {"exists": {"plan": _expand(sub["plan"], catalog, tenant, as_owner=True), "corr": sub["corr"]}}
    return pred
