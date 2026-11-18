"""Checks run after restoring a dump in the recovery drill."""


def check_orders(data):
    orders, lines = data.get("orders", {}), data.get("order_lines", {})
    problems = []
    sums = {}
    for key, line in lines.items():
        if line["order"] not in orders:
            problems.append(f"line {key} has no order {line['order']}")
        sums[line["order"]] = sums.get(line["order"], 0) + line["amount"]
    for oid, order in orders.items():
        if order["total"] != sums.get(oid, 0):
            problems.append(f"order {oid} total {order['total']} != lines {sums.get(oid, 0)}")
    return problems
