SELECT id, amount
FROM orders
WHERE customer_id IN (SELECT id FROM customers WHERE region = 'EU');
