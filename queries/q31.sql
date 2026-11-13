WITH RECURSIVE r(id, amount, lvl) AS (
  SELECT id, amount, 1 FROM orders WHERE amount > 500
  UNION ALL
  SELECT o.id, o.amount, r.lvl + 1 FROM orders AS o JOIN r ON o.customer_id = r.id WHERE r.lvl < 3
)
SELECT id, amount FROM r;
