-- Regioni senza geometria boundary nota (boundary IS NULL).
-- Solo VEN (id=4) ha boundary NULL nel seed: testa il NULL handling
-- sui tipi geometria.
-- Riferimento: test-e2e/sql/expected/README.md sezione 10.3.

SELECT id,
       code,
       name
FROM regions
WHERE boundary IS NULL
ORDER BY id;
