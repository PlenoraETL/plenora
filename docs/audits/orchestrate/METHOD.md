# Method - Second Run Protocol

La seconda run 0 -> 5 eredita la disciplina della Phase 5, ma deve andare piu'
in profondita'.

## Default Workflow

Per ogni target:

1. Preflight
   - inventario simboli;
   - call graph;
   - test coupling;
   - monkeypatch / patch.object / setattr;
   - inspect.getsource / AST scan;
   - logger/global state;
   - dead-code audit;
   - baseline test.

2. Strategy Proposal
   - cluster candidati;
   - rischio;
   - diff stimato;
   - rollback;
   - test gate atteso;
   - extraction vs no-op.

3. Implementation
   - un cluster coeso per commit;
   - no scope creep;
   - body-preserving se il batch e' refactor;
   - cambio comportamento solo se dichiarato e testato.

4. Stop Pre-Commit
   - file modificati;
   - diff size;
   - invarianti preservate;
   - test eseguiti;
   - rischi residui;
   - commit suggerito.

5. Closeout
   - CODE_MAP/docs;
   - decisioni architetturali;
   - carry-over;
   - test gate finale.

## Test Policy

Behavior-bound contracts > source-location-bound contracts.

Se un refactor corretto rompe un test:
- capire se il test protegge comportamento o posizione fisica;
- se protegge comportamento nel modo sbagliato, modernizzare il test in commit
  separato;
- poi fare il refactor;
- evitare carve-out artificiali solo per soddisfare un mock fragile.

Pattern source-location-bound da cercare sempre in preflight:

- `patch("...logger")`
- `monkeypatch.setattr(<module_or_alias>, "logger", ...)`
- `inspect.getsource(<module>)`
- `inspect.getsource(<class_or_method>)` quando l'assert dipende dal file fisico
- `ast.parse(Path(...).read_text())` su un file specifico
- monkeypatch su facade/module-level symbols che potrebbe non propagare nei sibling
  modules

Se uno di questi pattern blocca una extraction corretta, il default e':

1. stop;
2. test-only modernization in commit separato;
3. gate test invariato;
4. extraction in commit successivo.

Non unire test modernization e refactor nello stesso commit, salvo casi
minimi e dichiarati.

## No-Op Policy

No-op e' una decisione valida quando:
- spostare codice peggiora patchability;
- il file e' gia' un orchestrator coerente;
- il guadagno e' solo estetico;
- il rollback diventerebbe troppo ampio.

Ogni no-op deve essere motivato e documentato.

Esempi di no-op legittimi:

- orchestrator singolo che perderebbe patchability se spostato;
- utility cross-mixin minuscola che vive meglio nel core class;
- module-level utility con caller esterni diretti dove il re-export aggiunge
  solo rumore;
- extraction che riduce righe ma peggiora la coesione semantica.

## Commit Policy

- Commit atomici.
- Cluster coesi.
- Cap flessibile, ma ogni sforamento va motivato.
- Test gate proporzionato al rischio.
- Nessun push remoto automatico durante la fase di refactor.

## Depth Policy For The Second Run

La seconda run deve andare piu' in profondita' della prima, perche' la prima ha
reso il codice navigabile.

Quindi non limitarsi a "split file":

- verificare se i test proteggono il comportamento giusto;
- cercare duplicazioni reali tra moduli ora separati;
- consolidare policy comuni solo quando il comportamento e' coperto;
- classificare compat wrapper e fallback;
- rafforzare failure-mode tests;
- distinguere residuo intenzionale da debito reale.

La seconda run eredita la disciplina della prima, non il rumore. Commenti
temporanei, cutover notes e re-export "cheap insurance" vanno rivalutati quando
non servono piu'.

## Phase Template

Ogni fase della seconda run segue lo stesso schema:

1. baseline;
2. preflight;
3. proposal;
4. execution in cluster atomici;
5. stop pre-commit;
6. closeout;
7. carry-over esplicito alla fase successiva.
