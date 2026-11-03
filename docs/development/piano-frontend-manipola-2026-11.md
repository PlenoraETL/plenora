# Piano: correzioni frontend e redesign di Manipola

**Data:** 03/11/2026. **Branch:** `feat/frontend-audit-manipola-redesign`.

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

## Fase 4 — Redesign Manipola (Claude, con Codex in parallelo sulle fette indipendenti)

| Fetta | Contenuto | Dipende da |
|---|---|---|
| M0 | Decisione su React Flow v12 (`@xyflow/react`): se si fa, migrazione qui, prima delle altre fette | F3 |
| M1 | Nodi compatti (titolo modificabile e riassunto) e pannello laterale che ospita il `DynamicConfig` esistente | F3 |
| M2 | Palette per scopo, "＋" sull'uscita di un nodo, layout automatico (dagre/elk), minimappa | M1 |
| M3 | Pannello di anteprima per passo | B1, M1 |
| M4 | Pannelli Join, Filtro multiplo e Formula multipla; suggerimento Lookup | B2–B4, M1 |
| M5 | Griglia "Seleziona e rinomina" nell'Output e come passo | B5, M1 |
| M6 | Vista Passi, "Vedi in Passi/nel canvas", rami scollegati con "Collega", duplica ramo | M1 |
| M7 | Autosave uniforme con debounce, un solo Esegui, avviso per rami scollegati, input runtime (R9.1) | M1 |

⛳ Checkpoint dopo **M2**, **M5** e **M7**.

## Fase 5 — Resto dell'audit

R3 ed R4–R11 sulle altre pagine, le voci P e la rimozione del codice morto (D). Avrà un piano a parte, scritto dopo il collaudo di Manipola.

---

## Divisione del lavoro

- **Claude:** Fasi 1, 3 e 4 (frontend), integrazione, verifica, commit.
- **Codex:** Fase 2 (backend) in parallelo alla Fase 1 (file disgiunti); poi rilettura incrociata delle Fasi 1 e 3; poi le fette indipendenti di Fase 4.
- **Utente:** push, aggiornamento di 172.16.28.6, collaudo e decisioni (M0, P2).
