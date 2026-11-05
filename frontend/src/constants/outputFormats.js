/**
 * outputFormats.js — l'unico catalogo dei formati di uscita (Fase 5 T5, R8.11).
 *
 * Prima quattro elenchi diversi (form dinamico, finestra di esecuzione,
 * pubblicazione, nodo Output): si poteva pubblicare un default (`pdf`,
 * `zip`) che l'esecuzione non offriva e che il backend non scrive. Ora
 * tutti leggono questo; il test `tests/shared/outputFormats.contract.test.js`
 * verifica che ogni formato sia tra quelli che il backend scrive
 * (`eseguibili/output_persistence.py`, `supported`).
 */
export const OUTPUT_FORMATS = Object.freeze([
  { value: 'csv', label: 'CSV', fileLabel: 'CSV (.csv)', icon: '📊', geo: false },
  { value: 'json', label: 'JSON', fileLabel: 'JSON (.json)', icon: '📄', geo: false },
  { value: 'xlsx', label: 'XLSX', fileLabel: 'Excel (.xlsx)', icon: '📗', geo: false },
  { value: 'geojson', label: 'GeoJSON', fileLabel: 'GeoJSON (.geojson)', icon: '🌐', geo: true },
  { value: 'gpkg', label: 'GeoPackage', fileLabel: 'GeoPackage (.gpkg)', icon: '📦', geo: true },
  { value: 'shp', label: 'Shapefile', fileLabel: 'Shapefile (.zip)', icon: '🗺️', geo: true },
].map(Object.freeze));

const OUTPUT_FORMAT_VALUES = Object.freeze(OUTPUT_FORMATS.map(f => f.value));

export const DEFAULT_OUTPUT_FORMAT = 'xlsx';

const isOutputFormat = (value) => OUTPUT_FORMAT_VALUES.includes(value);

/**
 * Opzioni di un select: il catalogo, piu' il valore attuale se non c'e'
 * (un default pubblicato prima, per esempio `pdf`), mostrato com'e' ma non
 * selezionabile: va scelto un formato supportato.
 */
export function outputFormatOptions(current) {
  const options = OUTPUT_FORMATS.map(f => ({ value: f.value, label: f.fileLabel }));
  if (current && !isOutputFormat(current)) {
    return [{ value: current, label: `${current} (non supportato)`, disabled: true }, ...options];
  }
  return options;
}

/** Formato di partenza di un'esecuzione: il default se supportato. */
export const initialOutputFormat = (value) => (isOutputFormat(value) ? value : DEFAULT_OUTPUT_FORMAT);
