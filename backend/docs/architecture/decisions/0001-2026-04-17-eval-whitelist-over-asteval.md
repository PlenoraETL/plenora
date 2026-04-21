# ADR-0001: Validazione formula utente via whitelist AST anziche' libreria `asteval`

**Status**: Accepted
**Date**: 2026-04-17
**Deciders**: @CloudIA

## Context

L'operazione `op_formula` in `app/core/manipola/transforms/base.py` permette all'utente di definire una **colonna calcolata** via espressione Python stile-pandas (es. `col_a.str.upper() + "-" + col_b.astype(str)`). La formula arriva come stringa dal frontend canvas-ETL e viene valutata su un `DataFrame` pandas.

Il vincolo di sicurezza e': **l'utente e' autenticato ma il trust level non e' root**. Un admin malizioso (o un account compromesso) non deve poter eseguire codice arbitrario sul worker Python. RCE via formula e' un vettore classico (`__builtins__` escape, `__class__.__subclasses__()` chain, import gadget, lambda + gadget).

I requisiti concreti che la validazione deve soddisfare:

1. Consentire aritmetica, confronti, boolean logic, method call su pandas `Series.str.*` / `Series.dt.*` / top-level pandas (`pd.to_numeric`, `pd.to_datetime`, `pd.cut`, ecc.).
2. Rifiutare `import`, `exec`, `eval`, `open`, `getattr`, lambda/comprehension con gadget chain, f-string con expression embed, walrus operator, subscript, slicing, introspezione runtime (`__class__`, `__globals__`, `__builtins__`).
3. Fail-safe di default: costrutti **futuri** (es. match-case Python 3.10, exception group 3.11) che non esistono ancora nel codice devono essere rifiutati automaticamente, non ammessi "finche' qualcuno non li aggiunge alla blocklist".
4. Zero nuove dipendenze runtime se possibile — la superficie d'attacco cresce con il dependency tree.
5. Testabile in isolamento con test di regressione su CVE storici (escape sandbox via dunder chain).

Riferimento invarianti: `invariants.md §1.6` — policy modulo `app/core/estrai|manipola|arricchisci` e' "misto preview-vs-execute" ma **execute = fail-fast**; la formula appartiene a execute. `invariants.md §4.1` layer business "dato critico = fail-fast".

## Decision

Validazione via **whitelist positiva sui nodi AST** (`ast.parse(..., mode="eval")` + walk), con denylist secondaria su `Attribute` (dunder) e su `Name` (builtin pericolosi anche se passati come colonne omonime). L'eval effettivo avviene con `__builtins__={}` e `local_vars` che contengono solo `pd` + le colonne del DataFrame.

Codice: `app/core/manipola/transforms/base.py:341-475` — funzione `_validate_formula_ast` piu' i set `_ALLOWED_AST_NODES`, `_FORBIDDEN_ATTR_NAMES`, `_FORBIDDEN_NAMES`, `_SAFE_PANDAS_ATTRS`.

La chiamata e' obbligatoria **prima** di `eval()`: `op_formula` invoca `_validate_formula_ast(formula)` e solleva `ValueError` se la formula non e' consentita. Nessun path alternativo che saltella la validazione.

## Rationale

- **Whitelist > denylist** per superficie attacco sconosciuta. I nodi AST che non compaiono in `_ALLOWED_AST_NODES` sono rifiutati per default: walrus, lambda, f-string, list/set/dict comprehension, ternary `IfExp`, subscript, slice, try/except, yield/await, import, class/function def. Un costrutto futuro introdotto da Python 3.13+ e' rifiutato automaticamente fino a review esplicita.
- **Due layer di difesa**: whitelist AST blocca la struttura, denylist su `Attribute` (`__class__` etc.) e `Name` (`eval`, `exec`, `__import__`) blocca escape anche dove la struttura sarebbe formalmente ammessa (es. `col.__class__` e' un `Attribute` legittimo dal punto di vista AST ma `__class__` e' in `_FORBIDDEN_ATTR_NAMES`).
- **Zero dipendenze runtime**: `ast` e' stdlib, nessuna CVE tracking aggiuntivo, nessun lock-in, nessun upgrade coordinato con altre deps.
- **Auditabilita'**: le 4 collezioni (`_ALLOWED_AST_NODES`, `_FORBIDDEN_ATTR_NAMES`, `_FORBIDDEN_NAMES`, `_SAFE_PANDAS_ATTRS`) sono leggibili a colpo d'occhio e modificabili con un commit minimale + test di regressione dedicato.
- **Manutenibilita' ragionevole**: quando pandas aggiunge un metodo `Series.str.new_method` che un utente chiede, l'aggiunta e' una riga in `_SAFE_PANDAS_ATTRS` + test. Costo lineare nei metodi effettivamente richiesti, non esponenziale nella superficie.
- **Errori esplicativi**: `ValueError(f"Formula non consentita: nodo AST '{node_name}' non in whitelist")` guida l'utente a riformulare, senza leakare dettagli interni sensibili.

## Alternatives considered

- **`asteval` library** (Matthew Newville) — **Rejected**. Mantenuta da un singolo autore, storico di CVE su dunder access (es. `asteval.Interpreter` ammetteva `__import__` in versioni <0.9.24). Avremmo comunque dovuto scrivere wrapper per negare pandas method call pericolosi (es. `DataFrame.query` puo' eseguire codice). Guadagno netto: sostituiamo un validator noi-scritto con uno esterno che richiede lo stesso audit piu' tracking CVE upstream. Non ne vale il costo.
- **`RestrictedPython` (Zope)** — **Rejected**. Progettato per template language Zope, trasforma il bytecode con `ReplacedBy` per bloccare attribute access. Overhead di una trasformazione bytecode + una compile step in piu' per ogni formula; il codice risultante e' piu' difficile da debuggare per l'utente (stack trace su bytecode trasformato). Il nostro caso d'uso non giustifica la complessita'.
- **Subprocess sandbox con `seccomp-bpf` / `firejail` / `nsjail`** — **Rejected**. Cost di cold start per-formula e' prohibitivo su workload ETL (migliaia di formula per pipeline); serverless e' out-of-scope. Gestire il lifecycle del sandbox (cleanup processi, timeout, resource limits) aggiunge piu' complessita' operativa di quanto la minaccia giustifichi, dato che l'utente e' gia' autenticato.
- **Formula engine dedicato (espressioni JSON tipo JSONLogic, o DSL proprietario parsato con `lark` / `pyparsing`)** — **Rejected per Fase 0/1**. Ragionevole a lungo termine ma richiede definire completamente il linguaggio supportato (regole di typing, coercion, null-handling pandas-compatible), scrivere parser + runtime, e rompere backward-compat sulle formule gia' salvate in DB. Tradeoff costo/beneficio negativo per Fase 0; riconsiderabile se in futuro diventa un pain point (ADR di supersedimento).
- **Vietare l'operazione `op_formula`** — **Rejected**. Feature usata in produzione dai clienti (ICSC, RFI); rimuoverla e' una regressione funzionale. L'alternativa "calcola lato UI in JS" sposta il problema, non lo risolve, e richiede ri-calcolare al persist.

## Consequences

### Positive
- Nessuna nuova dipendenza runtime; dependency tree crypto/auth/sql rimane focalizzato.
- Controllo totale sulla superficie ammessa: audit policy = review di 4 set Python, non audit CVE esterno.
- Fail-safe di default su nodi AST futuri (Python 3.13+): costrutti sconosciuti bloccati finche' esplicitamente aggiunti.
- Tre difese concentriche: whitelist AST (struttura), denylist dunder (escape via `Attribute`), denylist `Name` (builtin via colonne omonime). Bypass richiede compromettere tutti e tre simultaneamente.
- Errori deterministici e localizzabili: `ValueError` con nome del nodo/attributo offending.

### Negative
- Maintenance burden lineare: ogni nuova esigenza pandas richiede aggiornare `_SAFE_PANDAS_ATTRS`. Mitigazione: e' un append di una stringa + test, non refactor.
- Falsi negativi possibili se un metodo pandas "safe-sembrato" ha side effect nascosti (es. `DataFrame.eval` sarebbe catastrofico se ammesso). Mitigazione: `_SAFE_PANDAS_ATTRS` e' conservativo e revisionato manualmente.
- Non supporta lambda / comprehension: alcune formule espressive richiedono workaround (es. `apply(lambda x: ...)` e' bloccato — richiederebbe decomposizione). Scelta deliberata: ammettere lambda apre immediatamente la chain `(lambda: 0).__class__.__mro__[-1].__subclasses__()`.

### Neutral
- Documentazione utente richiede esempi concreti di "cosa e' ammesso" perche' la whitelist e' stretta. Tracciato come follow-up (non blocker).
- Test di regressione dedicati su escape sandbox storici vivono in `backend/tests2/core/manipola/` (vedi repo per copertura attuale).

## References

- Source: `backend/app/core/manipola/transforms/base.py:341-475` — `_validate_formula_ast` + set ausiliari
- Source: `backend/app/core/manipola/transforms/base.py:483-...` — `op_formula`, sito di chiamata
- Commit introduttivo: `df35930` (2026-04-10) — "Security audit: 63 backend fixes + 10 frontend fixes + 258 unit tests"
- Invariants: `backend/docs/architecture/invariants.md` §1.6 (policy errore modulo `manipola` execute = fail-fast), §4.1 (layer business dato critico = fail-fast)
- Related: nessun ADR correlato in questo batch (ADR-0002 e ADR-0003 coprono SQL injection e crypto at-rest, aree adiacenti ma indipendenti dal parser formula)
