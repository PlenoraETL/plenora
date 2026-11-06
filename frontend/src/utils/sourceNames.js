/**
 * sourceNames — nome salvato dei nodi sorgente (UI-3 U9, conferma B-K9).
 *
 * REGOLA: il nome ATTUALE di una sorgente (progetto o file) viene dal
 * catalogo delle sorgenti dell'editor e sta nel campo TRANSITORIO
 * `_sourceName` (components/editor/editorNodeCatalog.withResolvedSourceNames);
 * `project_name` / `file_name` sono la copia salvata. Al salvataggio la copia
 * segue il nome attuale (utils/canvasSerializer): una sorgente rinominata non
 * resta col nome vecchio nel canvas salvato. `_sourceName: null` = sorgente
 * fuori dall'elenco: la copia salvata resta com'e'.
 */
const SOURCE_NAME_FIELDS = Object.freeze({ project_source: 'project_name', file_source: 'file_name' });

/** { field, name } da scrivere al salvataggio, o null. */
export function savedSourceName(node) {
  const field = SOURCE_NAME_FIELDS[node?.type];
  const current = node?.data?._sourceName;
  return field && current ? { field, name: current } : null;
}
