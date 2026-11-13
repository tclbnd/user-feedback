SELECT c.id,
  (SELECT SUM(o.amount) FROM orders AS o WHERE o.customer_id = c.id) AS total
FROM customers AS c;
