SELECT c.id, COALESCE(o.amount, 0) AS amt
FROM customers AS c
LEFT JOIN orders AS o ON o.customer_id = c.id AND o.status = 'paid'
WHERE c.region <> 'APAC';
