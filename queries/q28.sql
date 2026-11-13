SELECT o.status,
  SUM(CASE WHEN oi.discount > 0 THEN oi.qty ELSE 0 END) AS discounted_qty
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
GROUP BY o.status;
