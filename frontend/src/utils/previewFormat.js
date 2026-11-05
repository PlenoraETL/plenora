/**
 * previewFormat — regole comuni alle anteprime dati (passo, modale, nodo
 * Output): quante righe chiedere e come mostrare i numeri.
 */
import { formatNumber } from './format';

/** Righe richieste dall'anteprima del passo e dalla modale di anteprima. */
export const PREVIEW_ROW_LIMIT = 100;

/**
 * Numero leggibile: i decimali binari (58.333333333336) si fermano a 4
 * cifre, con la virgola. Interi e non-numeri restano come sono (codici,
 * anni e id non prendono separatori).
 */
export function formatCellNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || Number.isInteger(value)) return String(value);
  return formatNumber(value, { maxDecimals: 4, grouping: false });
}

/** Cella di anteprima come testo. */
export function formatPreviewValue(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return formatCellNumber(value);
}
