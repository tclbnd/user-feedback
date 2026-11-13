WITH t AS (SELECT id, amount FROM orders)
SELECT * FROM (
  WITH t AS (SELECT id, status FROM orders)
  SELECT * FROM t
) AS s;
