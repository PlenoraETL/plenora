/**
 * estraiJoins — il join tra tabelle di Estrai, che vive sull'ARCO
 * (edge.data, serializzato da utils/canvasSerializer). Regole uniche
 * (Blocco A, raccolta b):
 *
 *  - CHI E' UN JOIN: isJoinEdge. Un arco di dati verso una tabella o un
 *    layer; mai i binding degli input runtime, l'Output o i caricamenti.
 *  - BOZZA E DATI: joinDraftFromData legge qualunque forma salvata (anche la
 *    vecchia coppia singola source_column/target_column); joinDataFromDraft
 *    scrive SEMPRE tutti i campi del join, quelli dell'altro tipo a vuoto:
 *    passando da standard a spaziale (o il contrario) non resta niente.
 *  - COLONNE: JOIN_COLUMN_FIELDS elenca ogni campo che nomina una colonna
 *    (lato, tipo, tipo di join). Le scelte possibili (joinFieldOptions) e il
 *    controllo "(non trovata)" (joinProblems) passano da li': un campo nuovo
 *    va aggiunto alla tabella, e il test di enumerazione lo copre.
 *  - STATO: joinStatus = 'incomplete' | 'missing' | 'ok', dagli stessi
 *    problemi; canvas (joinEdgeLook), vista Passi e pannello lo leggono da qui.
 *  - NASCITA: newJoinEdge crea un arco "da completare": nessuna colonna
 *    scelta al posto dell'utente.
 */
import { isParameterBindingEdge } from './runtimeBindingEdges';
import { nodeRole } from './canvasGraph';
import { nodeTitle } from '../components/canvas/nodeDescriptors';
import { term } from './glossary';
import { JOIN_TYPES as CATALOG_JOIN_TYPES, newJoinType, readJoinType } from './joinTypes';

// Tipi di Join dal catalogo unico dei tre editor (utils/joinTypes, U21).
export const JOIN_TYPES = CATALOG_JOIN_TYPES
  .filter(t => t.values.estrai)
  .map(t => ({ value: t.values.estrai, label: `${t.label} (${t.short})`, description: t.description }));

// Funzioni applicabili alle colonne (condizioni e filtri ON), come il backend.
export const JOIN_FUNCTIONS = [
  { value: '', label: '—' },
  { value: 'TRIM', label: 'TRIM()' },
  { value: 'LTRIM_RTRIM', label: 'LTRIM(RTRIM())' },
  { value: 'UPPER', label: 'UPPER()' },
  { value: 'LOWER', label: 'LOWER()' },
  { value: 'COALESCE_EMPTY', label: "COALESCE(, '')" },
];

export const ON_FILTER_OPERATORS = [
  { value: '=', label: '=', needsValue: true },
  { value: '<>', label: '≠ (<>)', needsValue: true },
  { value: '>', label: '>', needsValue: true },
  { value: '<', label: '<', needsValue: true },
  { value: '>=', label: '≥ (>=)', needsValue: true },
  { value: '<=', label: '≤ (<=)', needsValue: true },
  { value: 'IS NULL', label: 'IS NULL', needsValue: false },
  { value: 'IS NOT NULL', label: 'IS NOT NULL', needsValue: false },
  { value: 'LIKE', label: 'LIKE', needsValue: true },
  { value: 'IN', label: 'IN', needsValue: true },
];

export const SPATIAL_PREDICATES = [
  { value: 'ST_Intersects', label: 'Interseca', description: 'Le geometrie si toccano o sovrappongono' },
  { value: 'ST_Contains', label: 'Contiene', description: 'La prima contiene completamente la seconda' },
  { value: 'ST_Within', label: 'Dentro', description: 'La prima è completamente dentro la seconda' },
  { value: 'ST_Touches', label: 'Tocca', description: 'Le geometrie si toccano al bordo' },
  { value: 'ST_Crosses', label: 'Attraversa', description: 'Le geometrie si incrociano' },
  { value: 'ST_Overlaps', label: 'Sovrappone', description: 'Le geometrie si sovrappongono parzialmente' },
  { value: 'ST_Equals', label: 'Uguale', description: 'Le geometrie sono spazialmente uguali' },
  { value: 'ST_Disjoint', label: 'Disgiunto', description: 'Le geometrie non hanno punti in comune' },
  { value: 'ST_Covers', label: 'Copre', description: 'La prima geometria copre la seconda' },
  { value: 'ST_CoveredBy', label: 'Coperto da', description: 'La prima geometria è coperta dalla seconda' },
  { value: 'ST_DWithin', label: 'Entro distanza', description: 'Geometrie entro una distanza specificata' },
];

const DEFAULT_PREDICATE = 'ST_Intersects';
const DEFAULT_DISTANCE = 100;

/**
 * Ogni campo del join che nomina una colonna. `list`: la riga ripetuta che
 * lo contiene (null = campo singolo); `side`: la tabella da cui si sceglie;
 * `kind`: 'column' (tutte le colonne) o 'geom' (solo geometrie).
 */
export const JOIN_COLUMN_FIELDS = Object.freeze([
  { id: 'condition.source_column', mode: 'standard', list: 'conditions', key: 'source_column', side: 'source', kind: 'column', label: 'Colonna sorgente' },
  { id: 'condition.target_column', mode: 'standard', list: 'conditions', key: 'target_column', side: 'target', kind: 'column', label: 'Colonna di destinazione' },
  { id: 'on_filter.column', mode: 'standard', list: 'on_filters', key: 'column', side: 'target', kind: 'column', label: 'Colonna del filtro ON' },
  { id: 'source_geom_column', mode: 'spatial', list: null, key: 'source_geom_column', side: 'source', kind: 'geom', label: 'Geometria sorgente' },
  { id: 'target_geom_column', mode: 'spatial', list: null, key: 'target_geom_column', side: 'target', kind: 'geom', label: 'Geometria di destinazione' },
]);

const OUTPUT_LIKE_JOIN_TYPES = new Set(['output', 'carica', 'carica_arcgis']);

/** L'arco e' un join di Estrai (dati verso una tabella o un layer)? */
export function isJoinEdge(edge, nodesById) {
  if (!edge || isParameterBindingEdge(edge)) return false;
  if (OUTPUT_LIKE_JOIN_TYPES.has(edge.data?.join_type)) return false;
  const source = nodesById.get(edge.source);
  const target = nodesById.get(edge.target);
  if (!source || !target) return false;
  return nodeRole(source) !== 'output' && ['source', 'step'].includes(nodeRole(target));
}

export const emptyCondition = () => ({ source_column: '', target_column: '', source_func: '', target_func: '' });
export const emptyOnFilter = () => ({ column: '', operator: 'IS NULL', value: '', func: '' });

/** Bozza modificabile dal dato salvato (qualunque forma). */
export function joinDraftFromData(data = {}) {
  const d = data || {};
  let conditions;
  if (Array.isArray(d.join_conditions) && d.join_conditions.length > 0) {
    conditions = d.join_conditions.map(c => ({
      source_column: c?.source_column || '', target_column: c?.target_column || '',
      source_func: c?.source_func || '', target_func: c?.target_func || '',
    }));
  } else if (d.source_column || d.target_column) {
    conditions = [{ ...emptyCondition(), source_column: d.source_column || '', target_column: d.target_column || '' }];
  } else {
    conditions = [emptyCondition()];
  }
  const onFilters = Array.isArray(d.on_filters)
    ? d.on_filters.map(f => ({ column: f?.column || '', operator: f?.operator || 'IS NULL', value: f?.value ?? '', func: f?.func || '' }))
    : [];
  return {
    mode: d.is_spatial ? 'spatial' : 'standard',
    join_type: readJoinType('estrai', d.join_type && !OUTPUT_LIKE_JOIN_TYPES.has(d.join_type) ? d.join_type : null),
    conditions,
    on_filters: onFilters,
    spatial_predicate: d.spatial_predicate || DEFAULT_PREDICATE,
    source_geom_column: d.source_geom_column || '',
    target_geom_column: d.target_geom_column || '',
    spatial_distance: d.spatial_distance ?? DEFAULT_DISTANCE,
  };
}

const needsValue = (op) => ON_FILTER_OPERATORS.find(o => o.value === op)?.needsValue ?? true;
const isComplete = (c) => Boolean(c.source_column && c.target_column);
const isEmptyCondition = (c) => !c.source_column && !c.target_column;

/**
 * Dato da scrivere sull'arco: TUTTI i campi del join, quelli che non
 * appartengono al tipo scelto a vuoto. Righe vuote e filtri senza colonna
 * non si scrivono.
 */
export function joinDataFromDraft(draft) {
  const blankSpatial = { spatial_predicate: null, source_geom_column: null, target_geom_column: null, spatial_distance: null };
  if (draft.mode === 'spatial') {
    return {
      join_type: draft.join_type, is_spatial: true,
      source_column: '', target_column: '', join_conditions: null, on_filters: null,
      spatial_predicate: draft.spatial_predicate || DEFAULT_PREDICATE,
      source_geom_column: draft.source_geom_column || null,
      target_geom_column: draft.target_geom_column || null,
      // Solo ST_DWithin usa la distanza: per gli altri predicati il backend
      // genererebbe un ST_Buffer indesiderato.
      spatial_distance: draft.spatial_predicate === 'ST_DWithin' ? Number(draft.spatial_distance) : null,
    };
  }
  const conditions = draft.conditions.filter(isComplete).map(c => ({
    source_column: c.source_column, target_column: c.target_column,
    source_func: c.source_func || '', target_func: c.target_func || '',
  }));
  const filters = draft.on_filters.filter(f => f.column).map(f => ({
    column: f.column, operator: f.operator, value: needsValue(f.operator) ? (f.value ?? '') : '', func: f.func || '',
  }));
  return {
    join_type: draft.join_type, is_spatial: false,
    // Prima condizione anche nei campi singoli: li leggono la validazione
    // del backend e i canvas salvati prima delle condizioni multiple.
    source_column: conditions[0]?.source_column || '',
    target_column: conditions[0]?.target_column || '',
    join_conditions: conditions.length ? conditions : null,
    on_filters: filters.length ? filters : null,
    ...blankSpatial,
  };
}

/** Colonne di una tabella per un tipo di campo; `known` = schema disponibile. */
export function joinFieldOptions(node, kind) {
  const columns = node?.data?.columns || [];
  const known = columns.length > 0;
  if (kind === 'geom') {
    const geom = columns.filter(c => String(c.type || '').startsWith('geometry')).map(c => ({ name: c.name, type: c.type }));
    const calc = (node?.data?.calculated_columns || [])
      .filter(c => String(c.output_type || '').startsWith('geometry'))
      .map(c => ({ name: c.name, type: c.output_type, isCalculated: true }));
    return { options: [...geom, ...calc], known };
  }
  return { options: columns.map(c => ({ name: c.name, type: c.type })), known };
}

/** Ogni riferimento a colonna della bozza, con il campo della tabella. */
function joinColumnRefs(draft) {
  const refs = [];
  for (const field of JOIN_COLUMN_FIELDS) {
    if (field.mode !== draft.mode) continue;
    if (field.list) {
      (draft[field.list] || []).forEach((row, index) => refs.push({ field, index, value: row?.[field.key] || '' }));
    } else {
      refs.push({ field, index: null, value: draft[field.key] || '' });
    }
  }
  return refs;
}

/** Il valore salvato non e' piu' tra le colonne della sua tabella? */
export function isColumnMissing(value, { options, known }) {
  return Boolean(value) && known && !options.some(o => o.name === value);
}

/**
 * Problemi della bozza, in ordine di gravita'. `code`:
 *  - 'incomplete': manca una scelta obbligatoria (arco "da completare");
 *  - 'missing':    una colonna scelta non c'e' piu' nella tabella;
 *  - 'invalid':    riga a meta' o distanza non valida.
 */
export function joinProblems(draft, sourceNode, targetNode) {
  const problems = [];
  const sides = { source: sourceNode, target: targetNode };
  for (const ref of joinColumnRefs(draft)) {
    const opts = joinFieldOptions(sides[ref.field.side], ref.field.kind);
    if (isColumnMissing(ref.value, opts)) {
      const where = ref.index === null ? '' : ` (riga ${ref.index + 1})`;
      problems.push({ code: 'missing', field: ref.field.id, index: ref.index, message: `${ref.field.label}${where}: «${ref.value}» non trovata. Scegline un'altra.` });
    }
  }
  // Fase 5 T5 (R8.17): niente si scarta in silenzio. Un filtro ON a meta'
  // (valore senza colonna, o colonna senza il valore che l'operatore chiede)
  // e i filtri ON di un join spaziale (che non li usa) sono problemi che
  // bloccano la scrittura con il loro messaggio.
  const filters = draft.on_filters || [];
  const usedFilters = filters.filter(f => f.column);
  if (draft.mode === 'spatial' && usedFilters.length > 0) {
    problems.push({ code: 'invalid', field: 'on_filters', message: `Il ${term('join').name} spaziale non usa i filtri ON (${usedFilters.length}): rimuovili per continuare.` });
  }
  if (draft.mode !== 'spatial') {
    filters.forEach((f, i) => {
      const hasValue = String(f.value ?? '').trim() !== '';
      if (!f.column && hasValue) {
        problems.push({ code: 'invalid', message: `Filtro ON ${i + 1}: scegli la colonna o rimuovilo.` });
      } else if (f.column && needsValue(f.operator) && !hasValue) {
        problems.push({ code: 'invalid', message: `Filtro ON ${i + 1}: scrivi il valore o rimuovilo.` });
      }
    });
  }
  if (draft.mode === 'spatial') {
    if (!draft.source_geom_column || !draft.target_geom_column) {
      problems.unshift({ code: 'incomplete', message: 'Scegli le due colonne geometriche.' });
    }
    if (draft.spatial_predicate === 'ST_DWithin') {
      const d = Number(draft.spatial_distance);
      if (draft.spatial_distance === '' || !Number.isFinite(d) || d < 0) problems.push({ code: 'invalid', message: 'La distanza deve essere un numero maggiore o uguale a zero.' });
    }
    return problems;
  }
  if (!draft.conditions.some(isComplete)) {
    problems.unshift({ code: 'incomplete', message: 'Scegli le colonne da collegare (sorgente = destinazione).' });
  }
  draft.conditions.forEach((c, i) => {
    if (!isComplete(c) && !isEmptyCondition(c) && draft.conditions.some(isComplete)) {
      problems.push({ code: 'invalid', message: `Condizione ${i + 1}: scegli entrambe le colonne o rimuovila.` });
    }
  });
  return problems;
}

/** Stato del join salvato sull'arco. */
export function joinStatus(data, sourceNode, targetNode) {
  const problems = joinProblems(joinDraftFromData(data), sourceNode, targetNode);
  if (problems.some(p => p.code === 'incomplete')) return 'incomplete';
  if (problems.some(p => p.code === 'missing')) return 'missing';
  return 'ok';
}

// Nomi mostrati: la clausola SQL ("LEFT JOIN") e il predicato spaziale in
// italiano ("Interseca"), mai la chiave tecnica.
const sqlJoinLabel = (joinType) => `${String(readJoinType('estrai', joinType)).toUpperCase()} JOIN`;
const spatialPredicateLabel = (value) => SPATIAL_PREDICATES.find(p => p.value === value)?.label
  || `${term('join').name} spaziale`;

const WARN_STYLE = { stroke: '#D97706', strokeWidth: 2, strokeDasharray: '6 4' };

/** Aspetto dell'arco sul canvas, derivato solo da dati e stato. */
export function joinEdgeLook(data, status) {
  if (status === 'incomplete') return { label: `⚠ ${term('join').name} da completare`, style: WARN_STYLE, animated: false };
  if (status === 'missing') return { label: '⚠ colonna non trovata', style: WARN_STYLE, animated: false };
  if (data?.is_spatial) {
    return { label: `🌐 ${spatialPredicateLabel(data.spatial_predicate)}`, style: { stroke: '#E91E63', strokeWidth: 2 }, animated: true };
  }
  return { label: sqlJoinLabel(data?.join_type), style: { stroke: '#2196F3', strokeWidth: 2 }, animated: true };
}

const lookCache = new WeakMap();

/**
 * Archi da mostrare: ogni join con l'aspetto del suo stato (colonne delle
 * tabelle comprese). Stessa identita' per gli archi che non cambiano.
 */
export function presentJoinEdges(edges, nodes) {
  const byId = new Map((nodes || []).map(n => [n.id, n]));
  return (edges || []).map((edge) => {
    if (!isJoinEdge(edge, byId)) return edge;
    const status = joinStatus(edge.data, byId.get(edge.source), byId.get(edge.target));
    const cached = lookCache.get(edge);
    if (cached && cached.status === status) return cached.shown;
    const shown = { ...edge, ...joinEdgeLook(edge.data, status) };
    lookCache.set(edge, { status, shown });
    return shown;
  });
}

let edgeSeq = 0;

/**
 * Arco di join nuovo, "da completare": nessuna colonna scelta. `suggestion`
 * (relazione del servizio ArcGIS) resta solo un suggerimento nel pannello.
 */
export function newJoinEdge({ source, target, join_type = newJoinType('estrai'), suggestion = null }) {
  edgeSeq += 1;
  const data = {
    ...joinDataFromDraft({ ...joinDraftFromData({}), join_type }),
    ...(suggestion ? { suggestion } : {}),
  };
  return { id: `edge_${Date.now()}_${edgeSeq}`, source, target, data, ...joinEdgeLook(data, 'incomplete') };
}

/**
 * P29 (UI-3): alias di una tabella aggiunta di nuovo (Join della tabella con
 * se stessa): `<tabella>_2`, `_3`, ... il primo libero tra gli alias
 * effettivi gia' nel canvas (alias o nome). Solo lettere, cifre e `_`: un
 * nome con lo schema (`dbo.ordini`) usa l'ultima parte. Stessa forma degli
 * alias che il backend assegna da solo ai canvas senza alias.
 */
export function selfJoinAlias(tableName, takenAliases = []) {
  // Come core/estrai/query_aliases: senza schema, solo \w, `t_` davanti a
  // una cifra, confronto senza maiuscole.
  let base = String(tableName || 'tabella').split('.').pop().replace(/[^A-Za-z0-9_]/g, '_') || 'tabella';
  if (/^\d/.test(base)) base = `t_${base}`;
  const taken = new Set([base, ...takenAliases].filter(Boolean).map(a => String(a).toLowerCase()));
  for (let n = 2; ; n += 1) if (!taken.has(`${base}_${n}`.toLowerCase())) return `${base}_${n}`;
}

/** Palette: la tabella nuova e' la sorgente, quella di partenza il target (come trascinando). */
export const paletteJoinEnds = (newNodeId, afterId) => ({ source: newNodeId, target: afterId });

/** Tabella correlata ArcGIS: verso e chiavi suggerite dalla relazione del servizio. */
export function relationJoinEnds(originId, newNodeId, relation) {
  const isOrigin = relation.role === 'origin';
  const own = relation.key_field;
  const related = relation.key_field_in_relationship_table || relation.key_field;
  return {
    source: isOrigin ? originId : newNodeId,
    target: isOrigin ? newNodeId : originId,
    join_type: relation.cardinality === '1:1' ? 'inner' : 'left',
    suggestion: {
      source_column: isOrigin ? own : related,
      target_column: isOrigin ? related : own,
      label: `relazione ${relation.name || ''} (${relation.cardinality || ''})`.replace(/\s+\(\)$/, '').trim(),
    },
  };
}

const wrapFunc = (expr, func) => {
  if (func === 'TRIM') return `TRIM(${expr})`;
  if (func === 'LTRIM_RTRIM') return `LTRIM(RTRIM(${expr}))`;
  if (func === 'UPPER') return `UPPER(${expr})`;
  if (func === 'LOWER') return `LOWER(${expr})`;
  if (func === 'COALESCE_EMPTY') return `COALESCE(${expr}, '')`;
  return expr;
};

/** Nome della tabella nel join (alias > nome), come la query. */
export const joinTableName = (node, fallback) => node?.data?.alias || node?.data?.table_name || node?.data?.layer_name || fallback;

/** Clausola ON in testo (anteprima nel pannello e riepiloghi). */
export function joinOnText(draft, sourceName, targetName) {
  if (draft.mode === 'spatial') {
    if (!draft.source_geom_column || !draft.target_geom_column) return '';
    const dist = draft.spatial_predicate === 'ST_DWithin' ? `, ${draft.spatial_distance}` : '';
    return `${draft.spatial_predicate}(${sourceName}.${draft.source_geom_column}, ${targetName}.${draft.target_geom_column}${dist})`;
  }
  const parts = draft.conditions.filter(isComplete).map(c => (
    `${wrapFunc(`${sourceName}.${c.source_column}`, c.source_func)} = ${wrapFunc(`${targetName}.${c.target_column}`, c.target_func)}`
  ));
  for (const f of draft.on_filters.filter(x => x.column)) {
    const col = wrapFunc(`${targetName}.${f.column}`, f.func);
    parts.push(needsValue(f.operator) ? `${col} ${f.operator} '${f.value}'` : `${col} ${f.operator}`);
  }
  return parts.join(' AND ');
}

const STATUS_TEXT = { incomplete: 'da completare', missing: 'colonna non trovata' };

/**
 * Voce di un join per la vista Passi, il pannello della tabella e
 * l'intestazione del pannello del join. `fromId`: la tabella da cui si
 * guarda (l'altra e' quella nominata).
 */
function joinEntry(edge, nodesById, fromId = edge.target) {
  const source = nodesById.get(edge.source);
  const target = nodesById.get(edge.target);
  const other = fromId === edge.source ? target : source;
  const status = joinStatus(edge.data, source, target);
  const draft = joinDraftFromData(edge.data);
  const kind = draft.mode === 'spatial'
    ? `${term('join').name} spaziale (${spatialPredicateLabel(draft.spatial_predicate)})`
    : sqlJoinLabel(draft.join_type);
  const otherTitle = other ? nodeTitle(other.type, other.data) : '?';
  const on = status === 'incomplete' ? '' : joinOnText(draft, joinTableName(source, 'sorgente'), joinTableName(target, 'destinazione'));
  return {
    id: edge.id,
    status,
    icon: status === 'ok' ? '🔗' : '⚠',
    title: `${term('join').name} con ${otherTitle}`,
    summary: STATUS_TEXT[status] ? `${kind} · ${STATUS_TEXT[status]}` : `${kind}${on ? ` · ${on}` : ''}`,
  };
}

/** Descrittore degli archi di Estrai per il guscio (NodeInspector, StepsView). */
export const estraiJoinEdges = Object.freeze({
  kind: term('join').name,
  icon: term('join').icon,
  matches: isJoinEdge,
  entry: joinEntry,
});
