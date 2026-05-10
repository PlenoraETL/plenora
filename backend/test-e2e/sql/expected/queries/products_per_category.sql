-- Conteggio prodotti per categoria (base + edge + NULL).
-- Include la categoria edge 'BOOKS' (1 solo prodotto, id=9).
-- Riferimento: test-e2e/sql/expected/README.md sezione 2.1 (esteso).

SELECT category,
       COUNT(*) AS n
FROM products
GROUP BY category
ORDER BY category;
