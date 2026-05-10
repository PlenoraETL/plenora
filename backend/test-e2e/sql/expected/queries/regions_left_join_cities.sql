-- LEFT JOIN regions con cities: numero di cities per regione.
-- Include la regione VEN (id=4) che NON ha cities → n_cities = 0.
-- Riferimento: test-e2e/sql/expected/README.md sezione 10.4.

SELECT r.id          AS region_id,
       r.code        AS region_code,
       r.name        AS region_name,
       COUNT(c.id)   AS n_cities
FROM regions r
LEFT JOIN cities c ON c.region_id = r.id
GROUP BY r.id, r.code, r.name
ORDER BY r.id;
