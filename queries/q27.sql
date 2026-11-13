WITH t AS (SELECT * FROM orders WHERE amount > 10)
SELECT t.*, c.name
FROM t
JOIN customers AS c ON c.id = t.customer_id;
