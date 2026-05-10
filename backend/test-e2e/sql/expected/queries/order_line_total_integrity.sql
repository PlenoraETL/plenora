-- Integrità order_items: identifica righe in cui line_total NON corrisponde
-- a qty * unit_price (edge case di riga inconsistente nel seed).
-- Sul blocco base (id 1..30) la query restituisce 0 righe.
-- Sul blocco edge è presente UNA riga inconsistente: id=33
-- (qty=1, unit_price=59.00, line_total=99.00 → expected=59.00).
-- Riferimento: test-e2e/sql/expected/README.md sezioni 4.1 e 7
-- (e la nota in test-e2e/sql/README.md riga "edge: 1 riga con line_total ≠ qty*unit_price").

SELECT id,
       qty,
       unit_price,
       line_total,
       (qty * unit_price) AS expected
FROM order_items
WHERE qty * unit_price <> line_total
ORDER BY id;
