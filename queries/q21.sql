SELECT id, amount,
  SUM(amount) OVER (PARTITION BY customer_id) AS cust_total,
  ROW_NUMBER() OVER (ORDER BY ordered_at) AS rn
FROM orders;
