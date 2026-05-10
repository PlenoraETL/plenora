-- Top 5 customer per numero di ordini.
-- Tie-break deterministico su c.id ASC (più customer hanno 2 ordini).
-- Riferimento: test-e2e/sql/expected/README.md sezione 3.1
-- estesa con i 5 ordini edge del customer 1 (orders 16..20).

SELECT c.id          AS customer_id,
       c.full_name   AS full_name,
       COUNT(o.id)   AS n_orders
FROM customers c
JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.full_name
ORDER BY COUNT(o.id) DESC, c.id ASC
LIMIT 5;
