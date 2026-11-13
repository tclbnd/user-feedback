SELECT status FROM orders
UNION
SELECT status FROM shipments
ORDER BY status
LIMIT 10;
