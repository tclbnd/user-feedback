WITH customers AS (
  SELECT id, region FROM customers WHERE segment = 'retail'
)
SELECT c.id, c.region
FROM customers AS c
WHERE c.region = 'EU';
