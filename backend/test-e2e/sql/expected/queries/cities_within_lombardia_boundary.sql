-- Cities geograficamente contenute nel boundary MultiPolygon di Lombardia.
-- Boundary LOM = rettangolo [long 8.5..10.5] × [lat 45.0..46.0].
-- Risultato: 5 cities (3 base + 2 edge tutte con region_id=1).
-- Riferimento: test-e2e/sql/expected/README.md sezione 10.5.

SELECT c.id,
       c.name,
       c.longitude,
       c.latitude
FROM cities c
JOIN regions r ON r.code = 'LOM'
WHERE ST_Within(c.geom, r.boundary)
ORDER BY c.id;
