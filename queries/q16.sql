SELECT s.cid, s.total
FROM (
  SELECT customer_id AS cid, SUM(amount) AS total
  FROM orders
  GROUP BY customer_id
) AS s
WHERE s.total > 100;
