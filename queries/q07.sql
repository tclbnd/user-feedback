SELECT customer_id, COUNT(*) AS cnt, SUM(amount) AS total
FROM orders
GROUP BY customer_id
HAVING COUNT(*) > 1;
