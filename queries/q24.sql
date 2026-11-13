SELECT p.category, SUM(oi.qty * oi.unit_price) AS revenue
FROM order_items AS oi
JOIN products AS p ON p.id = oi.product_id
JOIN orders AS o ON o.id = oi.order_id
WHERE o.status = 'shipped'
GROUP BY p.category;
