/**
 * runForm — come si esegue un Eseguibile: regola UNICA (UI conferma U14).
 *
 * Con un manifest nella versione attiva si compila il modulo dinamico
 * (ExecutableDynamicRunForm); senza, si carica un file con le colonne attese
 * (EseguibileExecuteWizard). Lista e dettaglio portano `has_manifest`,
 * calcolato dal backend con la sua regola unica (conferma finale G5,
 * core/eseguibili/db.manifest_present: manifest NON VUOTO; prima la lista
 * diceva `v.manifest IS NOT NULL`, sempre vero, e gli Eseguibili a file
 * singolo mostravano «Modulo»). Senza `has_manifest` (risposta vecchia) vale
 * la stessa regola sul `manifest`: un oggetto vuoto non e' un modulo. Prima
 * la card di /run decideva con `expected_columns_count` e la finestra con il
 * manifest: un Eseguibile col modulo e colonne legacy diceva «File con 2
 * colonne» e apriva il modulo. La card, la finestra e la lista leggono da qui
 * (tests/shared/esecutoreRun.contract.test.jsx).
 */

/** L'Eseguibile si esegue con il modulo dinamico? */
const manifestPresent = (manifest) => Boolean(manifest) && typeof manifest === 'object'
  && Object.keys(manifest).length > 0;

export const usesRunForm = (eseguibile) => (typeof eseguibile?.has_manifest === 'boolean'
  ? eseguibile.has_manifest
  : manifestPresent(eseguibile?.manifest));

/** Che cosa prepara chi esegue, in parole (card di /run e della lista). */
export function runInputSummary(eseguibile) {
  if (usesRunForm(eseguibile)) return 'Modulo da compilare';
  const raw = Number(eseguibile?.expected_columns_count);
  const n = Number.isFinite(raw) ? raw : 0;
  if (n <= 0) return 'File da caricare';
  return `File con ${n} ${n === 1 ? 'colonna' : 'colonne'}`;
}
