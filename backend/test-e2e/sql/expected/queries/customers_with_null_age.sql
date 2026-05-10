-- Customers con age NULL (NULL handling).
-- Una sola riga: customer id=21 'Null Age User' (blocco NULL del seed).
-- Riferimento: test-e2e/sql/expected/README.md sezioni 8.1 e 8.6.

SELECT id,
       full_name,
       email,
       age
FROM customers
WHERE age IS NULL
ORDER BY id;
