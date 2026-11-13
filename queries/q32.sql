WITH big_orders AS (
  SELECT o.id, o.customer_id, o.amount
  FROM orders AS o
  WHERE o.amount > (SELECT AVG(o2.amount) FROM orders AS o2)
)
SELECT c.name, bo.amount, s.carrier
FROM big_orders AS bo
JOIN customers AS c ON c.id = bo.customer_id
LEFT JOIN shipments AS s ON s.order_id = bo.id
WHERE EXISTS (
  SELECT 1 FROM order_items AS oi
  WHERE oi.order_id = bo.id AND oi.qty > 5
);
