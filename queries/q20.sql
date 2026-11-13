SELECT a.id, b.id AS other_id
FROM orders AS a
JOIN orders AS b ON b.customer_id = a.customer_id AND b.id > a.id;
