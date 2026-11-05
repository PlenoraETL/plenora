# Fase 5 — Resto dell'audit frontend (piano, 05/11/2026)

Raccolta completa: `docs/audits/fase5-raccolta-2026-11-05.md` (tre lettori indipendenti, ogni voce dell'audit del 03/11 verificata sul codice di `e4688ae6`).

## Stato di partenza

| Gruppo | Voci | Chiuse | Parziali | Aperte | N.p.a. | Nuovi difetti |
|---|---|---|---|---|---|---|
| R1–R4 (canvas, tipi, colonne, risorse) | 63 | 25 | 11 | 26 | 1 | 14 |
| R5–R8 (errori, modali, navigazione, form) | 48 | 6 | 9 | 33 | 0 | 14 |
| R9–R11, P, D, U, rimandati, residui A | 143 | 37 | 7 | 96 | 3 | 8 + 9 codice morto |
| **Totale** | **254** | **68** | **27** | **155** | **4** | **45** |

Canvas, tipi di nodo e colonne (R1, R2, R3 colonne) sono chiusi per costruzione dalle Fasi 1–4 e dal Blocco A; P1 (XSS), P2 (test finto) e i 12 rimandati sono chiusi. Restano soprattutto le **pagine di sistema** (utenti, ticket, log, admin, connessioni, data source, file, eseguibili, schedule, dashboard), mai toccate dal redesign, e l'infrastruttura comune che manca (risorse/task asincroni, errori, modali, navigazione, form).

## Regola di lavoro

Per ogni tranche: **correzione unica** (meccanismo comune + test di enumerazione che vieta il ritorno del difetto) → **un giro di conferma** indipendente → commit → deploy quando c'è abbastanza materiale → collaudo dal vivo. Tetto di 6 giri di lettura per tranche.

## Tranche (in ordine di rischio)

### T1 — Sicurezza, permessi, sessione
R10 (tutte: azioni visibili a chi non può, `role ===` grezzi, eliminazione gruppo che confronta l'id, Bonifica orfani globale, `window.prompt`, gruppo proprietario dal client, segreti degli header REST in `node.data`, devtools in produzione, allegati), R5.5 (logout che non azzera gli store), cache di modulo non svuotate al logout (R5.4, R1-N4, R5-N3), password minima 12 come il backend (R5-N1, N2), 401 su `/auth/login` (R5-N9), guardia XSS estesa a `bindTooltip(variabile)` e `divIcon({html})` (R9-N5).
**Meccanismo:** `can.*` unico con test che vieta `role ===` fuori da `permissions.js`; registro delle cache azzerate da un solo `resetSession()`; segreti mai nel canvas (riferimento alla connessione).

### T2 — Risorse e task asincroni, esecuzioni
R4 (tutte), R9 (stati, polling, download, busy, revoca, rollback ottimistico), anteprime con id di richiesta (R1-N2), race degli schemi (R1-N3), Annulla/progresso/chiusura dell'esecuzione in Estrai e Manipola (R4.11, R9.4, R9-N4), report errori (R9-N3), Eseguibili con la finestra unica «Prima di avviare» e la stima (residuo A, BA2).
**Meccanismo:** `useResource` (id di richiesta, stati loading/error/empty/ready, `<ListState>`) e `useTask` (polling con stop, 404/5xx/revoked, annulla); test di architettura che vieta `useEffect`+fetch a mano nelle pagine.

### T3 — Errori e messaggi
R5 restanti (401 tardivo e ripetuto, `getMe`, boundary con Sentry, normalizzazione unica: niente `err.message` grezzo nei toast), R5.9–R5.10.
**Meccanismo:** `requestErrorMessage` unico (già introdotto in F3) esteso a tutte le pagine; test che vieta `err.message` nei toast.

### T4 — Modali, wizard, navigazione
R6 (stack per Esc/overflow, id duplicati, salto di step senza validazione, doppio submit, overlay del Wizard, × attiva durante l'invio, Ctrl+Z/Y sotto le modali, `window.confirm`/prompt), R7 (`useBlocker` per tasto Indietro e Ctrl+K, LogViewer), R5-N10 (tre regole diverse per "finestra aperta"), N14 (focus).
**Meccanismo:** stack dei modali unico (`useDialog`) che possiede Esc, focus, scorciatoie e overlay; guard di navigazione unico.

### T5 — Form e input
R8 (ProjectInputNode, ConnectionSetsPanel, Pivot, cron/fusi orari, `parseInt||0`, cataloghi duplicati, `input_N`/`count_N`, ecc.), R5-N4 (Colonne attese che azzerano i tipi), N11–N13.
**Meccanismo:** form dichiarativi con buffer locale e un solo convertitore per tipo di campo; test di enumerazione dei campi.

### T6 — Canvas residui
R1.4 (upload paralleli di extra_files), R1.9 (stato locale stantio dopo undo), R2.9–R2.10, R3.12 (rinomina che riscrive i letterali nelle espressioni), R3.13 + R1-N14 (anteprima SQL di Estrai), R3.15–R3.21, R1/R3 parziali, N1 (form completi solo nel pannello), R9-N1 (chiavi legacy del Lookup riscritte come testo), R9-N2 (cambio operazione che lascia i binding), BA1 (tipi previsti del Lookup), BA3.

### T7 — Formati, pagine di sistema, accessibilita', prestazioni, codice morto
R11 (ora UTC come locale, 9 formattatori di dimensione, enum grezzi), U (Dashboard, pagine di sistema, badge, linee guida G1–G5), accessibilita' e prestazioni, P restanti non assorbite dalle tranche sopra, D e codice morto nuovo (con test che trova export e CSS senza uso).

## Esecuzione

T1 e T3 sono piccole e toccano file diversi: partono subito. T2 e T4 sono l'infrastruttura su cui poggiano le pagine: dopo T1/T3. T5–T7 a seguire. Deploy proposti: dopo T1+T3, dopo T2+T4, dopo T5–T7.
