-- Conteggio righe per ogni tabella del seed (base + edge + NULL + volume).
-- Riferimento: test-e2e/sql/README.md (tabella distribuzione) e
-- test-e2e/sql/expected/README.md sezione 8.

SELECT 'regions'     AS table_name, COUNT(*) AS n FROM regions
UNION ALL
SELECT 'cities',      COUNT(*) FROM cities
UNION ALL
SELECT 'customers',   COUNT(*) FROM customers
UNION ALL
SELECT 'products',    COUNT(*) FROM products
UNION ALL
SELECT 'orders',      COUNT(*) FROM orders
UNION ALL
SELECT 'order_items', COUNT(*) FROM order_items
UNION ALL
SELECT 'employees',   COUNT(*) FROM employees
UNION ALL
SELECT 'events_log',  COUNT(*) FROM events_log
UNION ALL
SELECT 'routes',      COUNT(*) FROM routes
ORDER BY table_name;
