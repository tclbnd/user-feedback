SELECT COUNT(*) AS n, AVG(amount) AS avg_amt
FROM orders
WHERE status = 'paid';
