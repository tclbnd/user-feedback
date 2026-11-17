"""Choose a SubPlan or a decorrelated join for every subquery."""
from decorrelate import Decorrelated
from engine import SubPlan


def plan(query, ctx, rewrite=True):
    labels = []

    def visit(sub, outer, exists=False, top=True):
        if rewrite and top:
            resolver = Decorrelated(sub, ctx, outer, exists)
            labels.append(f"Decorrelated {sub['as']}")
        else:
            resolver = SubPlan(sub, ctx, exists)
            labels.append(f"SubPlan {sub['as']}")
        ctx.resolvers[id(sub)] = resolver
        scan(sub.get("where", []), outer | {sub["as"]}, top=False)
        resolver.prepare()

    def scan(nodes, outer, top=True):
        for node in nodes:
            if "scalar" in node:
                visit(node["scalar"], outer, top=top)
            elif node.get("op") in ("exists", "not_exists"):
                visit(node["query"], outer, exists=True, top=top)
            else:
                scan([node[k] for k in ("left", "right", "arg") if k in node] + node.get("args", []), outer, top)

    scan(query["select"] + query.get("where", []), {query["as"]})
    return labels
