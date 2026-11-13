SELECT o.id AS order_id, s.carrier
FROM shipments AS s
RIGHT JOIN orders AS o ON o.id = s.order_id;
