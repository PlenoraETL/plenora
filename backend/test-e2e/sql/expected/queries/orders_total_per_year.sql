-- Somma total_amount raggruppata per anno di order_date.
-- L'ordine 21 (total_amount NULL) viene ignorato dalla SUM.
-- Anni coperti: 1999 (legacy), 2023 (end-of-day), 2024 (base + edge), 2025 (futuro).
-- Riferimento: test-e2e/sql/expected/README.md sezioni 7 e 8.4.

SELECT EXTRACT(YEAR FROM order_date)::INT AS order_year,
       COUNT(*)                            AS n_orders,
       SUM(total_amount)                   AS sum_total
FROM orders
GROUP BY EXTRACT(YEAR FROM order_date)
ORDER BY order_year;
