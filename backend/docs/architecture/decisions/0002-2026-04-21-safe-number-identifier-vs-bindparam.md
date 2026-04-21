# ADR-0002: f-string SQL con validatori `_safe_number` / `_safe_identifier` vs SQLAlchemy `bindparam`

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL costruisce query SQL dinamicamente a partire da un canvas utente (modulo `estrai`). Il motivo e' che la forma della query cambia per esecuzione: quali colonne proiettare, quali JOIN, quali GROUP BY, quali traduttori di funzioni spaziali (`ST_Centroid`, `ST_Transform`, ecc.). La logica di generazione vive in `app/core/estrai/query_generator.py` e in `app/db/sql_functions.py` (traduttori DSL → SQL nativo per ciascun dialetto: postgres, oracle, sqlserver, mysql).

Due classi di valori interpolati:

1. **Numeri** (tolleranze, SRID, distanze, ordinali): devono finire nel SQL come letterale numerico, non come apostrofato (`SELECT ST_Buffer(geom, 100)` e' diverso da `ST_Buffer(geom, '100')` su alcuni dialetti).
2. **Identificatori** (nomi di colonna, alias, nomi di tabella qualificati): devono finire nudi o tra doppi apici; non possono essere binded come parametri.

Il vincolo di sicurezza e' SQL injection. L'utente del canvas e' autenticato ma — come per ADR-0001 — **il suo trust level non e' root**. Un input come `column_name = "geom); DROP TABLE plenora.users; --"` non deve produrre DDL.

Il paradigma canonico per evitare injection e' il **prepared statement con parametri binded**. Pero' i parametri binded funzionano solo per **valori**, non per **struttura** (identifier, keyword, operator). SQLAlchemy offre `bindparam` per i valori e `text()` con `:name` per inlining, ma per costruire dinamicamente la forma della query si finisce comunque per concatenare stringhe — shifting il problema, non eliminandolo.

## Decision

Usare **f-string SQL con validatori di input stretti** per i contesti dove `bindparam` non e' applicabile (struttura, numeri in funzioni native dialetto-specifiche). Validatori concreti, centralizzati in `app/db/sql_functions.py`:

- `_safe_number(value, kind="float"|"int")` — cast a numerico, `ValueError` su non-numerico. Rifiuta `"1; DROP TABLE"`.
- `_safe_identifier(value)` — whitelist regex `^[A-Za-z_][A-Za-z0-9_]{0,62}$`. Rifiuta spazi, virgolette, semicoloni, commenti.
- `_safe_qualified_identifier(value)` — come sopra ma ammette una sola dot (`table.column`). Max 2 parti.
- `_safe_compare_operator(value)` — whitelist di `=, !=, <>, <, >, <=, >=`. Rifiuta `"= '1' OR 1=1 --"`.

Inoltre in `query_generator.py` esistono varianti piu' permissive per identificatori con trattini (comuni in campi importati da GeoJSON/API):

- `_is_valid_quoted_identifier(name)` — ammette `[A-Za-z0-9_\-]`, max 128 char, blocca keyword DDL/DML pericolose (`drop`, `delete`, `truncate`, `insert`, `update`, `alter`, `create`, `exec`, `execute`, `union`, `grant`, `revoke`).
- `_safe_identifier(name)` (metodo su `QueryGenerator`, diverso dal modulo `sql_functions`) — delega a `_is_valid_identifier` oppure a `_is_valid_quoted_identifier`, emette la forma corretta (`nome` o `"nome-con-trattino"`).

I valori dato (es. filtro WHERE `colonna = 'X'`) invece **continuano a passare per parametri binded** via `psycopg2` / `sqlalchemy.text(... :param)`. `_sql_escape` esiste per casi residui dove il valore finisce letteralmente tra apici singoli (rare), e il suo docstring lo circoscrive esplicitamente a quell'uso.

## Rationale

- **`bindparam` non copre struttura**. Non esiste in SQL standard un modo di parametrizzare il nome di una colonna. Ogni approccio "evita f-string totalmente" richiede un builder (SQLAlchemy Core, `sqlglot`, ecc.) che alla fine produce lo stesso pattern di whitelist internamente — lo sposta di strato.
- **Whitelist > sanitizzazione**. Un validator che **rifiuta** input fuori pattern e' intrinsecamente piu' sicuro di uno che **ripulisce** input malformati (un bug nella sanitizzazione lascia passare; un bug nella whitelist al peggio rifiuta un input legittimo → errore visibile, non silenzioso).
- **Centralizzazione**. Tutti i call-site di interpolazione strutturale passano per una delle 4 funzioni in `sql_functions.py` o per `_safe_identifier` su `QueryGenerator`. Audit della superficie = lettura di ~50 righe di pattern + whitelist keyword.
- **Regex stretta per Postgres**: max 63 char e' il limite `NAMEDATALEN-1` di Postgres di default. Coincidenza utile: la regex riusa lo stesso vincolo del motore sottostante.
- **Errori esplicativi**: ogni validator solleva `ValueError` con il valore offending e la regola violata (`"_safe_identifier: 'x y' contiene caratteri non validi (ammessi solo [A-Za-z0-9_], inizio con lettera o _)"`). L'operatore di supporto capisce in 5 secondi cosa e' stato rifiutato.

## Alternatives considered

- **SQLAlchemy Core full rewrite (Table, Column, select(), func.*)** — **Rejected per Fase 0/1**. Avrebbe costretto a tradurre anche le funzioni spaziali dialetto-specifiche (`ST_*`) via `func.*` + `registerGeneric` o simili. Le divergenze tra postgres/PostGIS, oracle/Spatial, sqlserver/Geometry type sono profonde (nomi diversi, parametri in ordini diversi, return-type diversi). Riscrivere i traduttori come `func` + `@compiles` per-dialect = ~3-4x il codice attuale e un `sqlalchemy[geoalchemy2]` in piu' nel dependency tree. Riconsiderabile se SQLAlchemy 2.x semplifica il compile hook, ma non blocker di Fase 0.
- **sqlglot / sql-tree manipulation** — **Rejected**. sqlglot e' pensato per parsing + trasformazione di SQL esistente, non per generazione da scratch. Usarlo significherebbe comunque costruire un AST programmatico equivalente a quello che facciamo in `QueryGenerator`, piu' una dipendenza maintained da un singolo autore (nota velocita' di evoluzione, breaking change tra minor release). Costo di adozione + lock-in non giustificato.
- **Custom DSL parsato con `lark`** — **Rejected**. Stesso argomento di ADR-0001 per le formule: riscrivere completamente il linguaggio + runtime + retro-compat con canvas salvati e' un progetto in se', non una security fix.
- **Vietare tutto il path dinamico, esporre solo endpoint "query preset"** — **Rejected**. Il canvas-ETL *e'* la feature; rimuoverla e' regressione funzionale.

## Consequences

### Positive
- Nessuna nuova dipendenza (`re` e' stdlib).
- Superficie di audit piccola e statica: 4 regex + 1 set di keyword pericolose + 1 set di operatori ammessi.
- Errori deterministici, localizzabili e testabili in unita' (test di regressione dedicati in `tests2/security/` — attuali + futuri).
- I validator si compongono: `_safe_qualified_identifier` e' definito in termini di `_QUALIFIED_RE` che riusa la grammatica di `_IDENTIFIER_RE`. Patchare uno di loro propaga.
- Retro-compat con SQL custom dei clienti: la forma emessa e' standard SQL, nessuna riga di config esistente si rompe.

### Negative
- Maintenance burden: aggiungere un dialetto con naming convention diversa (es. identifier con `$` ammesso) richiede un validator dedicato, non un bool toggle. Accettato — i dialetti supportati sono 4 e non crescono spesso.
- I call-site sono molti (grep `_safe_identifier` ritorna 20+ linee solo in `query_generator.py`): se un nuovo operatore viene aggiunto al generator, va coperto con test e code-review per assicurare che passi per il validator. Mitigazione: test di regressione `tests2/security/unit/test_estrai_sql_injection.py` esercita input attack-pattern comuni.
- `_sql_escape` resta nel codice per i pochi casi di valore letterale tra apici; il suo docstring esplicitamente lo circoscrive. Rimuoverlo in un refactor futuro e' lecito ma non urgente.

### Neutral
- Il pattern differisce leggermente in `sql_functions.py` (max 63 char, no trattini) vs `query_generator.py` (max 128 char, trattini ammessi tra `""`). Divergenza giustificata: `sql_functions.py` emette identifier nativi Postgres, `query_generator.py` deve anche gestire dati importati da GeoJSON/API che legittimamente hanno `-`.

## References

- Source: `backend/app/db/sql_functions.py:84-179` — `_IDENTIFIER_RE`, `_QUALIFIED_RE`, `_safe_number`, `_safe_identifier`, `_safe_qualified_identifier`, `_safe_compare_operator`, `_COMPARE_OPERATORS`
- Source: `backend/app/core/estrai/query_generator.py:1620-1658` — `_is_valid_quoted_identifier`, `_safe_identifier` (metodo)
- Source: `backend/app/db/sql_functions.py:56-66` — `_sql_escape` con docstring che ne circoscrive l'uso
- Tests: `backend/tests2/security/unit/` — pattern di injection standard (comment injection, stacked statements, keyword escape)
- Related: ADR-0001 (whitelist AST per formula — stesso pattern applicato a un dominio diverso)
- Invariants: `backend/docs/architecture/invariants.md` §1 (policy fail-fast modulo `estrai` execute)
