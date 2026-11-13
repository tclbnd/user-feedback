WITH eu AS (
  SELECT id, name, segment FROM customers WHERE region = 'EU'
)
SELECT id, name FROM eu;
