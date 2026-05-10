-- Customers senza alcun ordine (LEFT JOIN orders + WHERE o.id IS NULL).
-- 12 customer:
--   base: id 3, 8, 12 (3)
--   edge: id 13..20 (8)
--   NULL: id 22 (1)
-- Riferimento: test-e2e/sql/expected/README.md sezione 8.7.

SELECT c.id,
       c.full_name
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
WHERE o.id IS NULL
ORDER BY c.id;
