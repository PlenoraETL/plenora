-- Categorie con più di un prodotto.
-- Esclude la categoria edge 'BOOKS' che contiene il solo prodotto id=9.
-- Riferimento: test-e2e/sql/expected/README.md sezione 2.1
-- (esteso a base+edge+NULL: 13 prodotti totali) e
-- test-e2e/sql/README.md (products: BOOKS solitario per HAVING COUNT > 1).

SELECT category,
       COUNT(*) AS n
FROM products
GROUP BY category
HAVING COUNT(*) > 1
ORDER BY category;
