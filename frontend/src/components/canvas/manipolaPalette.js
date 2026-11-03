/**
 * manipolaPalette — gruppi della palette di Manipola (Fase 4, M2).
 *
 * Le 71 operazioni del registry sono raggruppate per SCOPO (cosa vuoi
 * fare) invece che per Base/Spaziale/Privacy. Un'operazione nuova nel
 * registry che non compare in PURPOSE_OF finisce in "Altre operazioni":
 * non sparisce mai dalla palette.
 */

export const PURPOSES = ['Pulisci', 'Filtra', 'Calcola', 'Combina', 'Riorganizza', 'Geometria', 'Privacy'];

const PURPOSE_OF = {
  // Pulisci
  text_normalize: 'Pulisci', fill_na: 'Pulisci', replace: 'Pulisci', distinct: 'Pulisci',
  dedup_advanced: 'Pulisci', string_pad: 'Pulisci', geo_make_valid: 'Pulisci', geo_clean_topology: 'Pulisci',
  // Filtra
  filter: 'Filtra', sample: 'Filtra', geo_within: 'Filtra',
  // Calcola
  formula: 'Calcola', conditional: 'Calcola', lookup: 'Calcola', type_cast: 'Calcola', date_extract: 'Calcola',
  add_row_number: 'Calcola', uuid_generator: 'Calcola', bin: 'Calcola', string_length: 'Calcola',
  string_extract: 'Calcola', concat_columns: 'Calcola', split_column: 'Calcola', window_function: 'Calcola',
  statistics: 'Calcola',
  // Combina
  join: 'Combina', concat: 'Combina', sjoin: 'Combina', cross_join: 'Combina', table_diff: 'Combina',
  // Riorganizza
  select_columns: 'Riorganizza', rename: 'Riorganizza', drop_columns: 'Riorganizza', reorder_columns: 'Riorganizza',
  aggregate: 'Riorganizza', pivot: 'Riorganizza', melt: 'Riorganizza', transpose: 'Riorganizza', sort: 'Riorganizza',
  flatten_json: 'Riorganizza',
  // Privacy
  md5_hash: 'Privacy', mask_data: 'Privacy',
};

const TOP = ['filter', 'formula', 'lookup', 'select_columns', 'aggregate', 'join', 'text_normalize'];

function purposeOf(opId, def) {
  if (PURPOSE_OF[opId]) return PURPOSE_OF[opId];
  if (opId.startsWith('geo_') || def?.category === 'spatial') return 'Geometria';
  if (def?.category === 'privacy') return 'Privacy';
  return 'Altre operazioni';
}

/** Gruppi della palette dal registry (null se non ancora caricato). */
export function buildManipolaPaletteGroups(registry) {
  const transforms = registry?.transforms || {};
  const toItem = (opId) => {
    const def = transforms[opId];
    return {
      id: opId, kind: 'transform', label: def?.name || opId, icon: def?.icon || '⚙️',
      desc: def?.description ? String(def.description).slice(0, 60) : '',
    };
  };
  const byPurpose = new Map([...PURPOSES, 'Altre operazioni'].map(p => [p, []]));
  for (const [opId, def] of Object.entries(transforms)) byPurpose.get(purposeOf(opId, def)).push(toItem(opId));

  const groups = [];
  const top = TOP.filter(op => transforms[op]).map(toItem);
  if (top.length) groups.push({ label: 'Più usate', items: top, onlyWhenEmpty: true });
  for (const [label, items] of byPurpose) {
    if (items.length) groups.push({ label, items: items.sort((a, b) => a.label.localeCompare(b.label, 'it')) });
  }
  groups.push({
    label: 'Unisci e uscite',
    items: [
      { id: 'multi_join', kind: 'multi_join', label: 'Multi-Join', icon: '🔗', desc: 'unisci più sorgenti' },
      { id: 'result', kind: 'result', label: 'Output', icon: '📁', desc: 'file finale della pipeline' },
      { id: 'carica', kind: 'carica', label: 'Carica su database', icon: '💾', desc: "dopo l'Output" },
    ],
  });
  return groups;
}

export const MULTI_INPUT_OPS = ['join', 'concat', 'sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference', 'table_diff'];
