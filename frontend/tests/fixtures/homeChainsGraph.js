/**
 * Risposta di GET /dashboard/graph come quella di produzione (09/11/2026,
 * i 19 progetti del mockup approvato della home per catene): una
 * connessione, 12 Estrai, 5 Manipola, 2 Eseguibili.
 */
const at = (day) => `2026-${day}T08:00:00+00:00`;
const run = (day, status = 'success') => ({ status, at: at(day) });

const estrai = (id, name, day, extra = {}) => ({
  id, name, project_type: 'estrai', default_connection_id: 'conn-1', tags: [],
  connections: [{ id: 'conn-1', kind: 'db' }], source_project_ids: [], file_source_ids: [],
  datasources: [], loads: [], tables: [], canvas_corrupted: false,
  last_run: run(day), last_success_at: at(day), schedules: 0, schedules_active: 0, updated_at: at(day),
  ...extra,
});
const manipola = (id, name, sources, day, extra = {}) => ({
  id, name, project_type: 'manipola', default_connection_id: null, tags: [],
  connections: [], source_project_ids: sources, file_source_ids: [], datasources: [],
  loads: [], tables: [], canvas_corrupted: false,
  last_run: run(day), last_success_at: at(day), schedules: 0, schedules_active: 0, updated_at: at(day),
  ...extra,
});

export const SC8 = ['sa', 'sb', 'sc', 'sd', 'se1', 'se2', 'sf1', 'sf2'];
const SC8_NAMES = ['SC_A_patrimonio_unita', 'SC_B_contratti', 'SC_C_valutazioni', 'SC_D_superfici_eav', 'SC_E1_canoni', 'SC_E2_transazioni', 'SC_F1_progetti', 'SC_F2_interventi'];

export const PRODUCTION_GRAPH = Object.freeze({
  projects: [
    estrai('ia', 'immobili attivi', '11-07', { loads: ['db'], tables: ['afm_rm', 'afm_bl'] }),
    manipola('iapi', 'immobili attivi per istituto', ['ia'], '11-09'),
    ...SC8.map((id, i) => estrai(id, SC8_NAMES[i], '09-29')),
    estrai('sg', 'SC_G_atti', '09-29'),
    manipola('scm', 'SC_scheda_manipola', [...SC8], '09-10'),
    manipola('cscm', 'Copia di SC_scheda_manipola', [...SC8, 'sg'], '11-04'),
    estrai('amm', 'SC_A_patrimonio_unita_AMM', '08-05'),
    estrai('afe', 'immobili attivi affittati/sfitti', '07-20'),
    manipola('afm', 'immobili attivi affittati/sfitti', ['afe'], '07-21'),
    manipola('afc', 'Copia di immobili attivi affittati/sfitti', ['afe'], '07-16'),
    estrai('tk', 'SC_H_ticket', '09-29'),
    estrai('cat', 'immobili attivi affittati/sfitti con categoria', '07-21'),
  ],
  eseguibili: [
    { id: 'ex-iapi', name: 'immobili attivi per istituto', project_type: 'manipola', source_project_id: 'iapi', inputs_count: 0, has_manifest: true, active_version: 1, updated_at: at('11-09') },
    { id: 'ex-amm', name: 'SC_A_patrimonio_unita_AMM', project_type: 'estrai', source_project_id: 'amm', inputs_count: 1, has_manifest: true, active_version: 1, updated_at: at('08-05') },
  ],
  unavailable: [],
});

export const PRODUCTION_CONNECTIONS = new Map([['conn-1', { name: 'ARCHIBUS_EMULE', kind: 'db' }]]);

/** Una copia modificabile della risposta di produzione. */
export const productionGraph = () => JSON.parse(JSON.stringify(PRODUCTION_GRAPH));
