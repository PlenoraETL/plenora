/**
 * canvasTypes.js — Mirror dei Literal canvas BE.
 *
 * Single source of truth FE per i valori enumerati condivisi col
 * backend (`backend/app/shared/canvas_types.py`). Ogni costante
 * qui DEVE matchare la corrispondente VALID_X lato Python — il
 * contract test `tests/contract/canvasTypes.contract.test.js`
 * verifica l'allineamento via snapshot.
 *
 * Convention:
 *  - I valori sono `Object.freeze([...])` di string canoniche, NON
 *    array di {value, label, desc}: l'etichetta UI vive nei
 *    componenti che le visualizzano (label/desc possono essere
 *    cambiate senza toccare il contract).
 *  - Quando un Literal BE ha drift attivo col valore FE-visibile
 *    (es. REST_METHODS: BE accetta DELETE, FE non lo offre),
 *    esponi DUE costanti: `X_BE` (superset accettato dal Pydantic
 *    canvas) e `X_UI` (subset esposto in UI). Il drift e' esplicito,
 *    documentato nel commento, e tracciabile dal contract test.
 *
 * Bump policy: vedi `app/config.py:API_VERSION`. Allineamento
 * con `app/shared/canvas_types.py` controllato dal contract test.
 */

// Node types del canvas.
export const NODE_TYPES = Object.freeze([
  'source', 'result', 'project_source', 'file_source', 'feature_source',
  'transform', 'api_enrich', 'staticValues', 'carica', 'carica_arcgis',
  'multi_join', 'comment', 'datasource_node',
]);

// 6 column types base. I sottotipi geometry (geometry:point, ecc.)
// vivono in `utils/columnUtils.js` perche' la normalizzazione UI ha
// regole proprie (display label, icona).
export const COLUMN_TYPES_BASE = Object.freeze([
  'int', 'real', 'str', 'bool', 'date', 'geometry',
]);

// Write mode CARICA (DB). Lista completa accettata dal canvas Pydantic.
// `ScheduleWizard` espone solo il subset CRON-safe (append/replace/upsert).
export const WRITE_MODES = Object.freeze([
  'create', 'replace', 'append', 'upsert', 'update', 'truncate_insert',
]);

// Destination type CARICA. Tre valori canonici, NON e' drift
// FE↔BE: `arcgis` e' emesso dal factory `caricaArcgis` di
// `canvasSerializer.js` come parte del Node serializzato (Node.type=
// 'carica_arcgis' + dest_type='arcgis'). Il CaricanNode UI semplice
// (CARICA su DB/REST) offre solo db/rest perche` arcgis ha il suo
// componente dedicato CaricanArcGISNode; entrambi i percorsi
// arrivano allo stesso `dest_type` literal lato BE per il branch
// logic dell'executor (`workers/tasks/carica_task.py:293`).
export const DEST_TYPES = Object.freeze(['db', 'arcgis', 'rest']);
// Subset offerto dal nodo CaricanNode UI (selettore dest_type 2-way):
// arcgis ha il suo componente dedicato e non e' selezionabile qui.
export const DEST_TYPES_CARICAN_UI = Object.freeze(['db', 'rest']);

// Transaction granularity DB.
export const TRANSACTION_MODES = Object.freeze(['all_or_nothing', 'per_batch']);

// Conflict policy CARICA. DRIFT NOTO: il canvas Pydantic accetta
// ["abort", "skip", "update"] (probabile bug pre-esistente), mentre
// l'enum executor `ConflictPolicy` e il FE usano "force". Qui
// esponiamo il valore FE-canonical ("force") — il fix lato BE
// (rinominare "update" -> "force") e' task separato.
export const CONFLICT_POLICIES = Object.freeze(['abort', 'skip', 'force']);

// REST HTTP method. Post fix-drift: BE e UI allineati. CARICA su
// DELETE non ha semantica e DELETE non era mai stato esposto in UI.
export const REST_METHODS = Object.freeze(['POST', 'PUT', 'PATCH']);

// REST batching: invio batch unico vs per-record.
export const REST_BATCH_MODES = Object.freeze(['batch', 'per_record']);

// Multi-join semantica.
export const MULTI_JOIN_TYPES = Object.freeze(['inner', 'left', 'outer']);
