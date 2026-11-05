/**
 * lookupFill — «Riempi con i valori presenti» della Mappa valori (Lookup;
 * Blocco A, F3, raccolta c).
 *
 * REGOLA
 *  - I valori vengono dal server (POST /projects/{id}/nodes/{node}/distinct):
 *    canvas CORRENTE dell'editor (anche non salvato), colonna scelta per nome
 *    e id, risolta nell'ingresso del passo, stessa preparazione
 *    dell'esecuzione, al massimo FILL_LIMIT valori distinti.
 *  - Il testo di ogni valore e' quello restituito: la forma di testo UNICA
 *    del backend (shared/execution/lookup_mapping.lookup_value_text: 5.0 ->
 *    "5", date "2024-01-05"), quella con cui il Lookup confronta le colonne
 *    numeriche, booleane, data e durata; le colonne di testo si confrontano
 *    col testo esatto. Nessuna conversione qui.
 *  - VALORE UTILIZZABILE (regola unica, `isUsableValue`): stringa NON VUOTA,
 *    SENZA trim. Il backend confronta le chiavi esattamente, quindi " " e
 *    " a" sono valori reali (anche nelle mappe salvate prima). La usano il
 *    riempimento, la riga vuota (`isEmptyRow`), la validazione e il
 *    salvataggio (`toSavedRows`). Il null e il testo vuoto non diventano
 *    righe: si contano e si dicono all'utente; si gestiscono con «Valori
 *    non mappati».
 *  - Si aggiungono solo i valori mancanti, in fondo, nell'ordine del server,
 *    alle righe CORRENTI al ritorno della lettura (le modifiche fatte durante
 *    l'attesa restano); le righe esistenti non si toccano.
 *  - Una riga aggiunta e' una PROPOSTA neutra (`proposed: true`, «diventa»
 *    vuoto, segnaposto "da tradurre"): non entra nella mappa salvata finche'
 *    l'utente non scrive un «diventa» utilizzabile (`isPendingProposal`);
 *    cancellarlo la riporta a proposta. Cosi' riempire non cambia mai il
 *    risultato, qualunque sia «Valori non mappati» (keep/null/value/assente)
 *    e qualunque sia il contratto numerico (lookup_is_numeric).
 *  - Le proposte sono legate alla colonna da cui vengono (`source`,
 *    `fillSourceKey`: id della colonna, altrimenti il nome). Se la colonna
 *    del Lookup cambia, le proposte non compilate si tolgono
 *    (`dropOrphanProposals`); le righe compilate restano. Una lettura che
 *    torna dopo il cambio di colonna non aggiunge niente.
 *  - Le proposte non compilate vivono SOLO nelle righe del pannello: non
 *    entrano nella configurazione, quindi nemmeno nella bozza locale ne'
 *    nella copia di sicurezza (sono suggerimenti: si rigenerano con Riempi).
 *  - Campione, troncamento e motivo (es. input file runtime: nessun dato)
 *    arrivano dal server e si mostrano.
 */
import { distinctNodeValues } from '../api';
import { serializeCanvas } from './canvasSerializer';
import useCanvasStore from '../store/useCanvasStore';
import { rowHasSavedKey, rowHasSavedTarget, savedCellValue } from './lookupMapping';
import { formatNumber } from './format';

export const FILL_LIMIT = 200;

/** Valori distinti dell'ingresso del passo, sul canvas corrente dell'editor. */
export function fetchPresentValues({ nodeId, column = null, columnId = null, limit = FILL_LIMIT }, store = useCanvasStore) {
  const st = store.getState();
  const canvas = serializeCanvas(st.nodes, st.edges, { mode: st.mode });
  return distinctNodeValues(st.projectId, nodeId, { canvas, column: column || null, columnId: columnId || null, limit });
}

/** Regola unica del «valore utilizzabile»: stringa non vuota, senza trim. */
export const isUsableValue = (v) => String(v ?? '') !== '';

/** Colonna da cui vengono le proposte: id se c'e', altrimenti il nome. */
export const fillSourceKey = ({ column = null, columnId = null } = {}) => {
  if (columnId) return `id:${columnId}`;
  return column ? `name:${column}` : null;
};

/** Riga vuota: ne' valore ne' «diventa» utilizzabili (si scarta). */
export const isEmptyRow = (r) => !hasKey(r) && !isUsableValue(r?.to) && !rowHasSavedTarget(r);

/** Chiave della riga: testo utilizzabile o chiave salvata non toccata (anche null, R9-U N1). */
export const hasKey = (r) => isUsableValue(r?.from) || rowHasSavedKey(r);

/**
 * Proposta ancora da compilare: riga aggiunta da Riempi (`proposed`, segno
 * d'origine che resta sulla riga) con «diventa» non utilizzabile. Scrivere
 * «diventa» la fa entrare nella mappa; cancellarlo la riporta a proposta.
 */
const isPendingProposal = (r) => Boolean(r?.proposed) && !isUsableValue(r?.to);

/** Righe da salvare: non vuote e non proposte da compilare (non cambiano il risultato). */
export const toSavedRows = (rows) => (rows || [])
  .filter(r => !isEmptyRow(r) && !isPendingProposal(r))
  // Chiavi e valori salvati non testuali restano col loro tipo finche' il
  // testo della cella non cambia (utils/lookupMapping, R9-U N1 / BA3).
  .map(r => ({ from: savedCellValue(r.from, r.saved_from), to: savedCellValue(r.to, r.saved_to) }));

/**
 * Toglie le proposte non compilate che vengono da un'altra colonna; le
 * righe compilate restano. Stesse righe (stesso array) se non c'e' niente da
 * togliere.
 */
export function dropOrphanProposals(rows, source) {
  const list = rows || [];
  const kept = list.filter(r => !(isPendingProposal(r) && r.source !== source));
  return kept.length === list.length ? list : kept;
}

/**
 * Righe della mappa con i valori presenti che mancano (proposte neutre,
 * legate a `source`, la colonna letta: fillSourceKey).
 * @returns {{rows: Array<{from,to,proposed?}>, added: string[],
 *   skipped: {nulls: number, blanks: number, blankValues: number}}}
 *   `nulls`/`blanks`: righe dei dati col null / con valori non utilizzabili;
 *   `blankValues`: quanti valori distinti non utilizzabili.
 */
export function mergePresentValues(rows, values, source = null) {
  const live = (rows || []).filter(r => !isEmptyRow(r));
  const have = new Set(live.map(r => String(r.from)));
  const added = [];
  const skipped = { nulls: 0, blanks: 0, blankValues: 0 };
  (values || []).forEach(v => {
    if (!v) return;
    if (v.is_null) { skipped.nulls += Number(v.count || 0); return; }
    const text = String(v.value ?? '');
    if (!isUsableValue(text)) {
      skipped.blanks += Number(v.count || 0);
      skipped.blankValues += 1;
      return;
    }
    if (have.has(text)) return;
    have.add(text);
    added.push(text);
  });
  return { rows: [...live, ...added.map(t => ({ from: t, to: '', proposed: true, source }))], added, skipped };
}

const fmt = (n) => formatNumber(n || 0);

/** Frase per l'utente sull'esito della lettura. */
export function describeFill(res, addedCount, skipped = { nulls: 0, blanks: 0, blankValues: 0 }) {
  const values = Array.isArray(res?.values) ? res.values : [];
  if (res?.reason && values.length === 0) return `Nessun valore letto: ${res.reason}.`;
  const parts = [];
  parts.push(addedCount === 0
    ? 'Nessun valore nuovo: quelli presenti sono già nella mappa.'
    : `${addedCount === 1 ? 'Aggiunto 1 valore da tradurre' : `Aggiunti ${fmt(addedCount)} valori da tradurre`}: scrivi «diventa» dove serve. Finché «diventa» è vuoto la riga non entra nella mappa e il risultato non cambia.`);
  if (skipped.nulls) parts.push(`Il valore vuoto (null) compare in ${fmt(skipped.nulls)} righe: non si scrive come riga, si gestisce con «Valori non mappati».`);
  if (skipped.blanks) {
    parts.push(`${skipped.blankValues === 1 ? 'Il testo vuoto compare' : `${fmt(skipped.blankValues)} valori vuoti compaiono`} in ${fmt(skipped.blanks)} righe: non si scrivono come riga, si gestiscono con «Valori non mappati».`);
  }
  if (res?.sampled) parts.push(`Letti da un campione di ${fmt(res.sampled_rows)} righe: potrebbero essercene altri.`);
  else if (res?.truncated) parts.push(`Mostrati i primi ${fmt(values.length)} valori distinti: la colonna ne ha altri.`);
  if (res?.reason) parts.push(res.reason);
  return parts.join(' ');
}
