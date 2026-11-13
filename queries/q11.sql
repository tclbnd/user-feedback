WITH a(x, y) AS (
  SELECT customer_id, amount FROM orders
),
b AS (
  SELECT x, SUM(y) AS total FROM a GROUP BY x
)
SELECT x, total FROM b WHERE total > 100;
