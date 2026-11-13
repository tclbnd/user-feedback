SELECT m.id, m.val, c.name
FROM metrics AS m
JOIN customers AS c ON c.id = m.id;
