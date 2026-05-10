-- Cities geograficamente contenute nel boundary MultiPolygon di Campania.
-- Boundary CAM = 2 poligoni:
--   main : [long 14.0..15.5] × [lat 40.5..41.5]
--   Capri: [long 14.20..14.30] × [lat 40.53..40.57]
-- Risultato: 5 cities (3 base + 2 edge), tutte nel poligono main.
-- Il poligono Capri non contiene nessuna city del seed (verifica
-- che il MultiPolygon-aware ST_Within supporti più componenti).
-- Riferimento: test-e2e/sql/expected/README.md sezione 10.6.

SELECT c.id,
       c.name,
       c.longitude,
       c.latitude
FROM cities c
JOIN regions r ON r.code = 'CAM'
WHERE ST_Within(c.geom, r.boundary)
ORDER BY c.id;
