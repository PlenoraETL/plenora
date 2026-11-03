# Piano: correzioni frontend e redesign di Manipola

**Data:** 03/11/2026 (aggiornato con l'estensione a tutti i canvas). **Branch:** `feat/frontend-audit-manipola-redesign`.

**Fonti:**
- audit: `docs/audits/frontend-audit-2026-11-03.md` (le sigle R*, P*, D* rimandano lì);
- mockup approvato: `Desktop\plenora-mokup\manipola-mockup.html`, fuori dal repo.

**Regola di lavoro** (raccolta → correzione unica → conferma): ogni fase chiude il suo gruppo di voci per costruzione (regola unica e test di enumerazione), poi **un solo giro di conferma**. Il giro di conferma è incrociato: Codex rilegge il lavoro di Claude e viceversa.

**Collaudo:** a ogni checkpoint ⛳ l'utente fa il push del branch e aggiorna `http://172.16.28.6/`; poi si collauda insieme nel browser. Il push lo fa l'utente.

---

## Fase 1 — Correzioni urgenti (Claude, solo frontend)

Voci puntuali, ad alto impatto e a basso rischio, che non dipendono dal redesign.

| Voce | Correzione |
|---|---|
| P1 XSS nel tooltip della mappa | Tooltip costruito con nodi DOM e `textContent` |
| R5.1 ToastContext instabile | Context delle azioni stabile e separato dalla lista dei toast; test sull'identità di `useToast()` |
| R4.10 `humanizeError` non importato | Import, più il gate di lint qui sotto |
| R5.6 Rules of Hooks (AllowedGroupsSelector, DynamicConfig) | Return anticipati spostati dopo gli hook |
| R8.12 lo schedule si riattiva in modifica | `enabled` conservato dallo schedule esistente |
| R9.12 toggle dello schedule | Aggiornamento ottimistico su `schedule.enabled`, refresh non bloccante |
| R10.6 + R5.8 condivisioni | Modifica bloccata finché le condivisioni non sono caricate; 404 riconosciuto da `response.status` |
| R7.3 link a `/api-connections` | Costante di route verso `/external-connectors` |
| P2 "Testa connessione" finto | Test reale se il backend ha l'endpoint, altrimenti il pulsante viene rimosso |

**Gate:** un controllo statico che intercetti identificatori non definiti e violazioni delle regole degli hook. Sarebbe stato sufficiente a trovare R4.10 ed R5.6.

**Verifica:** `vitest` + `vite build`. ⛳ **Checkpoint 1.**

## Fase 2 — Backend Manipola (Codex)

Aggiunte **retrocompatibili**: i canvas già salvati devono continuare a funzionare identici.

| Id | Aggiunta | Serve a |
|---|---|---|
| B1 | Anteprima fino a un nodo: `GET /projects/{id}/preview?node_id=…` esegue solo gli antenati del nodo | Anteprima per passo (M3) |
| B2 | Join: colonne da tenere per lato e gestione dei nomi uguali (`keep_left` / `keep_right` / `suffix`) | Join senza `_L`/`_R` (M4) |
| B3 | Filtro: lista di condizioni in AND e operatori `in` / `not_in` | Un filtro al posto di più nodi Filtra (M4) |
| B4 | Formula: lista di colonne `{nome, espressione}` | Una formula al posto di più nodi (M4) |
| B5 | Nuova operazione `select_columns`: tieni, rinomina, ordina | Griglia "Seleziona e rinomina" (M5) |

Ogni aggiunta deve avere:
- aggiornamento di `transforms_registry.json`;
- test del vecchio formato **e** del nuovo;
- tipi di colonna propagati anche in `canvas_schema_runtime`.

⛳ **Checkpoint 2** (backend in ambiente, frontend invariato).

## Fase 3 — Fondamenta del canvas (Claude; Codex rilegge)

- **R1:** azioni esplicite dello store (`addNodes`, `addEdges`, `updateEdge`, `removeEdges`, `updateNodeConfig` funzionale), ciascuna con dirty e snapshot; un canale `applyDerived` che non marca mai dirty; divieto di usare `setNodes` di React Flow dai nodi (test di architettura).
- **R2:** registro dichiarativo dei tipi di nodo (nome canonico, alias, campi con default e flag di persistenza). Factory, serializer ed edge handler si derivano da lì; test di round-trip su **ogni tipo e ogni campo**.
- **Esito atteso:** si chiudono R1.1–R1.12 ed R2.1–R2.10 nei tre editor, e Manipola non risulta più "modificato" appena aperto.

⛳ **Checkpoint 3:** regressione su Estrai, Manipola e Arricchisci.

## Fase 4 — Nuova interfaccia per TUTTI i canvas (Estrai, Manipola, Arricchisci)

**Decisioni dell'utente (03/11/2026):**
- migrazione a React Flow v12 (`@xyflow/react`);
- **sostituzione diretta**, senza interruttore vecchia/nuova interfaccia;
- la nuova interfaccia del mockup vale per **tutti e tre gli editor**, per uniformità.

**Architettura.** Una sola "shell" di canvas condivisa e, per ogni editor, solo la tabella dei suoi tipi di nodo. Questo chiude anche Q-04 (editor copiati e già divergenti). La shell comprende:
- nodo compatto (titolo, riassunto, stato);
- pannello laterale (inspector);
- palette;
- anteprima per passo;
- vista Passi;
- layout automatico, minimappa, rami scollegati;
- toolbar, salvataggio ed esecuzione.

| Fetta | Contenuto | Dipende da |
|---|---|---|
| M0 | Migrazione a `@xyflow/react` v12 dei tre editor, a interfaccia invariata | F3 |
| M1 | Shell condivisa: nodo compatto e inspector laterale; i form esistenti (DynamicConfig, pannelli di tabella/result/carica, ApiEnrich) spostati nell'inspector | M0 |
| M2 | Palette per editor (operazioni Manipola, tabelle/viste Estrai, connettori Arricchisci), "＋" sul nodo, layout automatico, minimappa | M1 |
| M3 | Anteprima per passo nei tre editor. Il backend B1 copre Manipola; per Estrai e Arricchisci serve l'estensione **B6** | M1, B1, B6 |
| M4 | Pannelli Join/Filtro/Formula multipli e suggerimento Lookup (Manipola); join tra tabelle (Estrai) | M1, B2–B4 |
| M5 | Griglia "Seleziona e rinomina" (Output di Manipola e Arricchisci, Result di Estrai) | M1, B5 |
| M6 | Vista Passi, "Vedi in Passi", rami scollegati con "Collega", duplica ramo (tre editor) | M1 |
| M7 | Toolbar uniforme, autosave con debounce, un solo Esegui, input runtime (R9.1) nei tre editor | M1 |

**Backend aggiuntivo B6:** anteprima per nodo anche per Estrai (SQL fino al nodo) e Arricchisci (pipeline fino al nodo), con lo stesso contratto di B1 (`?node_id=`).

⛳ Checkpoint dopo **M0**, **M2**, **M5** e **M7**.

### Stato M4+M5 (03/11/2026)

Fatti, con backend B7 (Colonne in uscita dell'Output), B8 (Lookup: mappatura
in ogni formato, righe non trovate), B9 (riferimenti alle colonne annidati).
Raccolta difetti con due lettori, correzione per regole, sei giri di lettura
(il tetto). Al quarto giro cambio di approccio: le regole del backend copiate
nel frontend (selezione, Join nuovo e vecchio, GROUP BY, tipi delle
aggregazioni) sono verificate su casi GENERATI dal Python
(`backend/tests2/contracts/column_plans_cases.json`), eseguiti da pytest e da
vitest.

Rimandati al blocco successivo (ognuno con raccolta, correzione e conferma):
1. **Id delle colonne nell'Output fino all'esecuzione.** Oggi l'Output lavora
   per nome (scelta voluta, coerente in anteprima, schema ed esecuzione): il
   nodo Output in esecuzione non riceve la mappa degli id, quindi una colonna
   configurata e poi rinominata a monte risulta "non trovata" e viene saltata.
   Approccio proposto: il frontend smette di ricalcolare le colonne a valle e
   usa lo schema del backend.
2. Join tra tabelle di Estrai nel pannello laterale (oggi resta la finestra).
3. Lookup: "Riempi con i valori presenti" (serve leggere i valori distinti).
4. Nota: schema e previsione applicano il GROUP BY anche a un Output di
   Arricchisci con dati vecchi o importati (l'esecuzione di Arricchisci non
   raggruppa e l'interfaccia non lo mostra).

Emersi dal collaudo dal vivo di M4+M5 (03/11/2026, copia di SC_scheda_manipola:
295.782 righe come prima, Filtra unito e Mappa valori convertita con
risultato identico, Colonne in uscita persistite e applicate al file):
5. **Lo schema previsto non coincide con l'esecuzione sui Join vecchi.** La
   griglia dell'Output elenca 8 colonne che il file non ha (catene di suffissi
   come `pcv_cd_id_L_L_L_L_L_L`): l'esecuzione rinomina o scarta i duplicati,
   l'inferenza dello schema no. Stessa radice del punto 1: le colonne a valle
   devono venire da cio' che l'esecuzione produce davvero.
6. Dopo "Trasforma in Mappa valori" il titolo salvato del passo resta
   "Condizionale (Case When)".
7. L'Output nel pannello laterale ha ancora la vecchia intestazione a scheda
   ("Output Manipola ×"); in M4 e' stata tolta solo ai passi di trasformazione.

**Stima:** circa 4 giornate (era circa 2 per il solo Manipola).

## Fase 5 — Resto dell'audit

R3 ed R4–R11 sulle altre pagine, le voci P e la rimozione del codice morto (D). Avrà un piano a parte, scritto dopo il collaudo di Manipola.

---

## Divisione del lavoro

- **Claude:** Fasi 1, 3 e 4 (frontend), integrazione, verifica, commit.
- **Codex:** Fase 2 (backend) in parallelo alla Fase 1 (file disgiunti); poi rilettura incrociata delle Fasi 1 e 3; poi le fette indipendenti di Fase 4.
- **Utente:** push, aggiornamento di 172.16.28.6, collaudo e decisioni (M0, P2).
