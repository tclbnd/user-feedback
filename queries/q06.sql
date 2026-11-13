SELECT c.*, o.amount AS amt
FROM customers AS c
JOIN orders AS o ON o.customer_id = c.id;
