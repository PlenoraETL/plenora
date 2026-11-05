/**
 * lookupMapping — regole condivise della "Mappa valori" (Lookup), lato
 * frontend: lettura della mappatura in ogni formato salvato e numeri
 * riconosciuti come li riconosce Python (backend shared/execution/lookup_mapping.py).
 */

/** Come Python float(): anche "1_000", "1e3", "inf", "nan". */
export const isPythonFloat = (v) => {
  const s = String(v ?? '').trim();
  return /^[+-]?((\d(_?\d)*)(\.(\d(_?\d)*)?)?|\.\d(_?\d)*)([eE][+-]?\d(_?\d)*)?$/.test(s)
    || /^[+-]?(inf|infinity|nan)$/i.test(s);
};

/**
 * Forma di testo di un valore JSON per il Lookup: la stessa del backend
 * (shared/execution/lookup_mapping.lookup_value_text): null -> null,
 * booleano True/False, numero intero senza decimali, testo invariato.
 */
function lookupValueText(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string') return v;
  if (typeof v === 'boolean') return v ? 'True' : 'False';
  if (typeof v === 'number') return Number.isNaN(v) ? null : String(v);
  return String(v);
}

/*
 * Chiavi e valori salvati NON testuali (Fase 5, T6, R9-U N1 / BA3).
 * Una mappa legacy puo' avere chiavi JSON vere (`true`, `5`, `null`): il
 * backend le usa COSI' sulle colonne di testo (percorso storico) e le
 * confronta come testo (`lookup_value_text`, "True"/"5") sulle colonne non
 * testo. Prima la prima modifica riscriveva TUTTE le righe come testo
 * (`"true"`, `"5"`) e cambiava il risultato di righe mai toccate; una chiave
 * null diventava una riga senza valore che bloccava il salvataggio.
 *
 * Regola: ogni cella non testuale porta il valore salvato (`saved_from` /
 * `saved_to`: { value, text }, `text` = forma del backend). Finche' l'utente
 * non cambia il testo della cella, si risalva il valore ORIGINALE con il suo
 * tipo; se lo cambia, vale il testo scritto (scelta esplicita). Una chiave
 * null salvata e' una chiave (si mostra vuota, con il segnaposto «null») e
 * non blocca il salvataggio.
 */
const savedCell = (v) => (typeof v === 'string' || v === undefined ? null : { value: v, text: lookupValueText(v) ?? '' });
const shown = (v) => (typeof v === 'string' ? v : (lookupValueText(v) ?? ''));

function row(from, to) {
  const out = { from: shown(from), to: shown(to) };
  const sf = savedCell(from);
  const st = savedCell(to);
  if (sf) out.saved_from = sf;
  if (st) out.saved_to = st;
  return out;
}

/** Mappatura Lookup in qualsiasi formato salvato -> righe {from, to}. */
export function readValueMapping(value) {
  if (Array.isArray(value)) {
    return value.filter(Boolean).map(r => row(r.from !== undefined ? r.from : r.old_name, r.to !== undefined ? r.to : r.new_name));
  }
  if (value && typeof value === 'object') return Object.entries(value).map(([from, to]) => row(from, to));
  return [];
}

/** Valore da salvare per una cella: l'originale se il testo non e' cambiato. */
export function savedCellValue(text, saved) {
  return saved && text === saved.text ? saved.value : text;
}

/** La riga ha una chiave: testo non vuoto o chiave salvata non toccata (anche null). */
export const rowHasSavedKey = (r) => Boolean(r?.saved_from) && r.from === r.saved_from.text;
export const rowHasSavedTarget = (r) => Boolean(r?.saved_to) && r.to === r.saved_to.text;
