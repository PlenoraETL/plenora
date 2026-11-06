/**
 * runForm — come si esegue un Eseguibile: regola UNICA (UI conferma U14).
 *
 * Con un manifest nella versione attiva si compila il modulo dinamico
 * (ExecutableDynamicRunForm); senza, si carica un file con le colonne attese
 * (EseguibileExecuteWizard). Il dettaglio porta il `manifest`, la lista
 * `has_manifest` (backend: stessa colonna, `v.manifest IS NOT NULL`). Prima
 * la card di /run decideva con `expected_columns_count` e la finestra con il
 * manifest: un Eseguibile col modulo e colonne legacy diceva «File con 2
 * colonne» e apriva il modulo. La card, la finestra e la lista leggono da qui
 * (tests/shared/esecutoreRun.contract.test.jsx).
 */

/** L'Eseguibile si esegue con il modulo dinamico? */
export const usesRunForm = (eseguibile) => Boolean(eseguibile?.manifest || eseguibile?.has_manifest);

/** Che cosa prepara chi esegue, in parole (card di /run e della lista). */
export function runInputSummary(eseguibile) {
  if (usesRunForm(eseguibile)) return 'Modulo da compilare';
  const raw = Number(eseguibile?.expected_columns_count);
  const n = Number.isFinite(raw) ? raw : 0;
  if (n <= 0) return 'File da caricare';
  return `File con ${n} ${n === 1 ? 'colonna' : 'colonne'}`;
}
