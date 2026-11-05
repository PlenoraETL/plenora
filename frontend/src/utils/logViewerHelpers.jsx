/**
 * logViewerHelpers — formati del Log Viewer (Fase 5 T7, R11.1, S5).
 *
 * Nessun formattatore proprio: data e ora dal modulo unico (utils/format),
 * nel fuso del browser con anno, secondi e sigla del fuso (gli orari dei
 * log sono UTC del server); il progetto per NOME, non per id troncato.
 */
import { formatDateTime } from './format';

/** Ora di una riga di log o di un'esecuzione: «05/11/2026, 14:03:12 CET». */
export function formatLogTime(ts) {
  return formatDateTime(ts, { seconds: true, zone: true });
}

/**
 * Nome del progetto di una riga: dal catalogo dei progetti visibili;
 * un progetto che non c'è più (o non visibile) mostra il suo id intero.
 * @param {string|undefined} id
 * @param {Map<string,string>|null} names
 */
export function projectLabel(id, names) {
  if (!id) return '—';
  return names?.get(String(id)) || id;
}
