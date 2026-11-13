SELECT c.id, name
FROM customers AS c
JOIN products AS p ON p.category = c.segment;
