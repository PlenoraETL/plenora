-- Conteggio ordini per status (base + edge + NULL).
-- L'ordine 21 ha total_amount NULL ma status='pending' (incluso).
-- Riferimento: test-e2e/sql/expected/README.md sezione 2.4 (esteso).

SELECT status,
       COUNT(*) AS n
FROM orders
GROUP BY status
ORDER BY status;
