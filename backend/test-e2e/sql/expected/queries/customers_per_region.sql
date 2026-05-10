-- Conteggio customers per regione (via JOIN customers → cities → regions).
-- I customer con city_id NULL (id 12, 20) NON compaiono in questa
-- aggregazione perché vengono filtrati dall'INNER JOIN su cities.
-- Riferimento: test-e2e/sql/expected/README.md sezione 2.2 (variante customers).

SELECT r.code        AS region_code,
       r.name        AS region_name,
       COUNT(cu.id)  AS n_customers
FROM regions r
JOIN cities ci  ON ci.region_id = r.id
JOIN customers cu ON cu.city_id  = ci.id
GROUP BY r.code, r.name
ORDER BY r.code;
