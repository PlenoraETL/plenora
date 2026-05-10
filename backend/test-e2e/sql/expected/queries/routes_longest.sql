-- Route più lunga del seed.
-- Include la route edge cross-meridian (id=5, NullIsland → DateLine,
-- length_km = 20015.00) che è ~25× più lunga della seconda.
-- Riferimento: test-e2e/sql/expected/README.md sezione 10.2.

SELECT id,
       name,
       length_km
FROM routes
ORDER BY length_km DESC
LIMIT 1;
