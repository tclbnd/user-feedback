SELECT id, name FROM customers
UNION ALL
SELECT id, sku FROM products;
