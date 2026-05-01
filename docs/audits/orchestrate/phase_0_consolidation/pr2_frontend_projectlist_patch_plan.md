# PR 2 — Frontend ProjectList Test Fix — Patch Plan (NOT applied)

Status: **plan only — read-only deep dive, no file changed.**

Scope batch CI red baseline, PR 2.

---

## 1. Conferma cause root (osservata sui sorgenti)

### 1.1 Struttura attuale del modal "Nuovo Progetto"

`frontend/src/pages/ProjectList.jsx:493–594` (modal `showModal`):

| Riga | Elemento | Role ARIA | Note |
| --- | --- | --- | --- |
| 511–526 | "Tipo" selector — **3 `<button>`** (estrai/manipola/arricchisci) | `button` × 3 | NON è un `<select>`. Click toggle stato |
| 528–538 | "Nome *" — `<input type="text">` | `textbox` | autoFocus |
| 540–549 | "Descrizione" — `<textarea>` | `textbox` | |
| 551–580 | "Connessione *" — `<select>` (**solo se** `newProjectType === 'estrai'`) | `combobox` | |

**Quando `newProjectType === 'estrai'` (default), il modal ha esattamente 1 combobox.** Quando il tipo è `manipola` o `arricchisci`, **0 combobox**.

### 1.2 Errore del test

`frontend/tests/pages/ProjectList.contract.test.jsx:109`:

```javascript
await userEvent.selectOptions(screen.getAllByRole('combobox')[1], 'conn-1');
```

- `screen.getAllByRole('combobox')` ritorna array di lunghezza 1.
- `[1]` (secondo elemento) → `undefined`.
- `userEvent.selectOptions(undefined, 'conn-1')` → tenta `.querySelectorAll` su `undefined` → `TypeError: Cannot read properties of undefined (reading 'querySelectorAll')`.

**Causa storica probabile**: il modal aveva il "Tipo" come `<select>` (sarebbe stato `[0]`) + "Connessione" come `<select>` (sarebbe stato `[1]`). Refactor che ha trasformato Tipo in 3 `<button>` ha rotto questa assunzione del test, ma il test non è stato aggiornato. Cambia il numero di combobox da 2 a 1, ma il test usa hardcoded `[1]`.

### 1.3 Stato a11y del select Connessione

`ProjectList.jsx:552–570`:

```jsx
<div className="pl__modal-field">
  <label className="pl__modal-label">Connessione *</label>
  <select
    className="pl__modal-select"
    value={newProjectConnId}
    onChange={e => setNewProjectConnId(e.target.value)}
  >
    ...
  </select>
</div>
```

Il `<label>` è **decorativo**: visualmente associato per layout ma **non programmaticamente** legato al `<select>`. Manca:
- `id` sul `<select>`
- `htmlFor` corrispondente sul `<label>`
- in alternativa, `aria-label` sul `<select>`

Conseguenza:
- Screen reader non annuncia "Connessione" cliccando sul select.
- Testing Library `getByRole('combobox', { name: /connessione/i })` **probabilmente non lo trova** (RTL fa match by `<label htmlFor>` o `aria-label`/`aria-labelledby`).

Stesso pattern accettato per "Nome" (riga 528) e "Descrizione" (540) — debt a11y diffuso, già citato in 0.5g §12.5 ma non bloccante.

### 1.4 Timing async

`mockDashboardData` setta:
```javascript
apiMock.listConnections.mockResolvedValue({ connections: [...] });
```

`ProjectList.loadData` è async; la `connections` state viene popolata dopo `await Promise.all([...])`. Il test fa `await screen.findByRole('button', { name: /nuovo progetto/i })` (aspetta che la dashboard renderizzi), poi clicca. A quel punto `connections` è popolato. Il modal apre e renderizza il `<select>` con le opzioni. Quindi **al momento di `getAllByRole('combobox')`, il select dovrebbe esserci**.

Ma c'è un caso limite: se il click su "Nuovo Progetto" apre il modal *prima* che React abbia ri-renderato con le connessioni risolte (microtask vs renderloop), il modal potrebbe renderizzarsi *senza* il `<select>` connessione. Però il default `newProjectType` è `'estrai'`, quindi il rendering condizionale a riga 551 dovrebbe valutare `true`.

In pratica: il test fallisce **deterministicamente** al `[1]`, non flakily. Quindi la causa primaria è l'indice fragile, non il timing. Però migrare a `findByRole` (che ha auto-wait) chiude entrambe le possibilità.

---

## 2. Patch minima proposta

Due modifiche, una per file:

### 2.1 Patch A — App code (a11y label association)

`frontend/src/pages/ProjectList.jsx:552–570`, aggiungere `id` + `htmlFor`:

```jsx
<div className="pl__modal-field">
  <label className="pl__modal-label" htmlFor="new-project-connection">
    Connessione *
  </label>
  <select
    id="new-project-connection"
    className="pl__modal-select"
    value={newProjectConnId}
    onChange={e => setNewProjectConnId(e.target.value)}
  >
    ...
  </select>
</div>
```

**Cambio**: 2 attributi aggiunti (`id` sul `<select>`, `htmlFor` sul `<label>`).
**Effetto user-visible**: nessuno (visivamente identico).
**Effetto a11y**: screen reader annuncia "Connessione" sul focus del select.
**Effetto test**: abilita `screen.getByRole('combobox', { name: /connessione/i })`.

### 2.2 Patch B — Test (target by name + auto-wait)

`frontend/tests/pages/ProjectList.contract.test.jsx:109`, sostituire:

```javascript
// prima
await userEvent.selectOptions(screen.getAllByRole('combobox')[1], 'conn-1');

// dopo
await userEvent.selectOptions(
  await screen.findByRole('combobox', { name: /connessione/i }),
  'conn-1',
);
```

**Cambio**: 1 chiamata sostituita.
**Effetto**:
- Niente più indice fragile (`[1]`).
- `findByRole` aspetta automaticamente (auto-retry su timeout default vitest).
- Targeting per accessible name → robusto a futuri refactor che aggiungano altri `<select>` al modal.

### 2.3 Diff sommario stimato

| File | Modifiche | LOC delta |
| --- | --- | ---:|
| `frontend/src/pages/ProjectList.jsx` | +2 attributi (id, htmlFor) sul Connessione field | +2 / -2 (riga riformattata) |
| `frontend/tests/pages/ProjectList.contract.test.jsx` | sostituisce 1 chiamata `selectOptions` | +4 / -1 |
| **Totale** | 2 file | **+6 / -3** |

Effort dev: ~15 min. Effort review + verifica CI: ~15 min. **~30 min totali.**

---

## 3. Alternative considerate

| # | Alternativa | Pro | Contro |
| --- | --- | --- | --- |
| 1 | Cambiare solo il test: `[1]` → `[0]` | 1 riga modificata. No app code touch. | Resta fragile a futuri `<select>` aggiunti al modal. Non chiude il debt a11y. |
| 2 | Cambiare solo il test: `findByRole` ma senza id/htmlFor (usa `screen.getByLabelText('Connessione *')` come fallback) | No app code touch. | RTL `getByLabelText` con `<label>` decorativo non funziona affidabilmente. Probabile fallimento del match. |
| 3 | **Patch A + B** (raccomandata) | Test stabile + a11y + zero regressioni runtime | Effort 30 min, tocca 2 file |
| 4 | Skip del test (`it.skip`) | Effort 1 min | Perde copertura del flusso più importante (create estrai project). Debt esplicito. NO. |

**Raccomandazione**: opzione **3 (Patch A + B)**. Effort piccolo, a11y migliorato (gap noto 0.5g), test future-proof.

---

## 4. Out-of-scope per PR 2

- ❌ A11y label/htmlFor per "Nome" e "Descrizione" (debt diffuso, ma non blocca CI). Citato in 0.5g §12.5.
- ❌ Refactor del modal struttura (es. estrazione `<NewProjectModal />`).
- ❌ Aggiungere `aria-label` al modal stesso.
- ❌ Refactor di altri test pages con pattern simile (es. `EstraiEditor.contract`, `ManipolaEditor.contract`).
- ❌ Test runtime locale (`npm test`) — vincolo no-runtime.
- ❌ Touch a `deploy.sh` o codice backend.

---

## 5. Decisione richiesta

1. **Approvazione patch A + B** (raccomandata, opzione 3 in §3)?
2. **Branch**: `chore/ci-fix-frontend-projectlist-test`?
3. **Target merge**: dopo che PR #2 (backend CI) sia stata mergeata, oppure in parallelo (PR indipendenti)?
4. **Test plan post-merge**: rerun CI deve mostrare frontend vitest 274/274 verde + backend pytest verde (post PR #2). Niente test runtime locale.

Resto in attesa. Niente file modificato. Niente test runtime. Niente deploy.

---

*End of PR 2 — Frontend ProjectList Patch Plan.*
