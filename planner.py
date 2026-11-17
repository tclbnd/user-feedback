"""Choose a SubPlan or a decorrelated join for every subquery."""
from decorrelate import Decorrelated
from engine import SubPlan


def plan(query, ctx, rewrite=True):
    labels = []

    def visit(sub, outer, exists=False):
        resolver = Decorrelated.build(sub, ctx, outer, exists) if rewrite else None
        if resolver is None:
            resolver = SubPlan(sub, ctx, exists)
            labels.append(f"SubPlan {sub['as']}")
        else:
            labels.append(f"Decorrelated {sub['as']}")
        ctx.resolvers[id(sub)] = resolver
        # Nested subqueries are planned the same way, against all enclosing aliases.
        scan(sub.get("where", []), outer | {sub["as"]})

    def scan(nodes, outer):
        for node in nodes:
            if "scalar" in node:
                visit(node["scalar"], outer)
            elif node.get("op") in ("exists", "not_exists"):
                visit(node["query"], outer, exists=True)
            else:
                scan([node[k] for k in ("left", "right", "arg") if k in node] + node.get("args", []), outer)

    scan(query["select"] + query.get("where", []), {query["as"]})
    return labels
