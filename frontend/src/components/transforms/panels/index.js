/**
 * Pannelli dedicati per operazione (Fase 4, M4/M5). Le operazioni senza
 * pannello usano il form generico dal registry (DynamicConfig).
 *
 * Un passo con input runtime collegati ai suoi parametri resta sul form
 * generico: i binding puntano ai parametri singoli (es. `value`) che i
 * pannelli sostituiscono con le liste (vedi TransformNode).
 */
import FilterPanel from './FilterPanel';
import FormulaPanel from './FormulaPanel';
import JoinPanel from './JoinPanel';
import SelectColumnsPanel from './SelectColumnsPanel';

export const OPERATION_PANELS = {
  filter: FilterPanel,
  formula: FormulaPanel,
  join: JoinPanel,
  select_columns: SelectColumnsPanel,
};

/**
 * Parametri del registry coperti da ogni pannello (anche quelli del
 * formato vecchio, che il pannello legge e converte). Un test confronta
 * questo elenco con il registry vero: un parametro nuovo nel backend non
 * puo' restare senza interfaccia.
 */
export const PANEL_PARAMS = {
  filter: ['column', 'operator', 'value', 'conditions'],
  formula: ['new_column', 'formula', 'items'],
  join: ['left_keys', 'right_keys', 'how', 'left_columns', 'right_columns', 'name_conflict'],
  select_columns: ['columns'],
};

export { default as LookupSuggestion } from './LookupSuggestion';
export { default as ColumnsGrid } from './ColumnsGrid';
