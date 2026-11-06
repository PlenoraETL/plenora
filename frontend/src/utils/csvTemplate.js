/**
 * csvTemplate — il file modello da scaricare per un Eseguibile (UI-4, U14):
 * un CSV con solo la riga delle intestazioni, le colonne che l'Eseguibile
 * si aspetta, nell'ordine dichiarato. Chi esegue lo riempie e lo carica.
 *
 * CSV standard (RFC 4180): separatore virgola, campi con virgola, virgolette
 * o a capo tra virgolette (le virgolette raddoppiate). BOM UTF-8 in testa:
 * Excel apre gli accenti correttamente.
 */
import { saveBlob } from './downloadBlob';

const field = (v) => {
  const s = String(v ?? '');
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** Testo del CSV modello per le colonne attese ([{ name }]). */
export function csvTemplateText(columns) {
  return `﻿${(columns || []).map(c => field(c?.name)).join(',')}\r\n`;
}

/** Nome del file modello: «modello_<nome>.csv», senza caratteri vietati. */
export const csvTemplateName = (name) => `modello_${String(name || 'eseguibile').trim().replace(/[\\/:*?"<>|\s]+/g, '_')}.csv`;

/** Scarica il CSV modello. */
export function downloadCsvTemplate(name, columns) {
  saveBlob(new Blob([csvTemplateText(columns)], { type: 'text/csv;charset=utf-8' }), csvTemplateName(name));
}
