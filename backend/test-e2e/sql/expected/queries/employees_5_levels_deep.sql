-- CTE ricorsiva sulla gerarchia employees: assegna a ciascun dipendente
-- la profondità rispetto al manager-radice (livello 1 = manager senza
-- manager_id; livello 5 = intern in fondo alla catena CEO→senior→
-- junior→helper→intern).
-- 17 employees totali distribuiti su 5 livelli:
--   livello 1: 3 (id 1, 2, 13 — manager senza manager_id)
--   livello 2: 6 (id 3, 4, 5, 14, 16, 17 — riportano a un livello 1)
--   livello 3: 6 (id 6, 7, 8, 9, 10, 15)
--   livello 4: 1 (id 11 — Kira Helper, riporta a Franco Junior Dev)
--   livello 5: 1 (id 12 — Lia Intern, riporta a Kira Helper)
-- Riferimento: test-e2e/sql/expected/README.md sezione 3.6 e
-- test-e2e/sql/README.md (employees: 4° + 5° livello in edge).

WITH RECURSIVE org AS (
    SELECT id,
           manager_id,
           name,
           role,
           1 AS depth
    FROM employees
    WHERE manager_id IS NULL
    UNION ALL
    SELECT e.id,
           e.manager_id,
           e.name,
           e.role,
           o.depth + 1
    FROM employees e
    JOIN org o ON e.manager_id = o.id
)
SELECT id, manager_id, name, role, depth
FROM org
ORDER BY depth, id;
