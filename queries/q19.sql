SELECT id,
  CASE WHEN amount > 100 THEN 'big' WHEN amount > 50 THEN 'mid' ELSE 'small' END AS size
FROM orders;
