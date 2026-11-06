/**
 * glossary.js — i nomi visibili dei tre editor canvas (Blocco A, regola 10).
 *
 * Una sola tabella: per ogni ENTITA' (tipo di nodo, voce della palette,
 * azione dell'editor) nome, icona e breve descrizione. La leggono il menu
 * "⋯" degli editor, le palette, nodeDescriptors (intestazione del pannello,
 * nodo compatto, vista Passi), i pulsanti dell'Output, le etichette degli
 * archi e i toast. Ogni entita' ha UN solo nome ovunque.
 *
 * Decisione dell'utente (04/11/2026): restano in inglese solo Output, Join,
 * Data Source e Multi-Join (scritti sempre cosi') e i nomi tecnici (SQL,
 * API, REST, ArcGIS, CSV, GeoJSON e simili formati e protocolli). Tutto il
 * resto e' in italiano. Il test tests/shared/glossary.contract.test.js
 * cerca i termini vietati nel testo visibile dei file degli editor.
 *
 * Le icone vengono dalla tabella per significato (utils/icons, UI-4 U24).
 *
 * Il catalogo delle operazioni Manipola arriva dal backend
 * (transforms_registry.json): TRANSFORM_TERMS ne sovrascrive, per chiave
 * operazione, i nomi e le descrizioni con termini inglesi. Si applica una
 * volta sola, quando il registry entra in cache (utils/transformRegistry).
 */

import { ICON } from './icons';
import { shortcutText } from './shortcuts';

/** Termini inglesi ammessi nel testo visibile (decisione dell'utente). */
export const ALLOWED_ENGLISH = Object.freeze({
  // Nomi di entita' che restano in inglese, sempre scritti esattamente cosi'.
  names: ['Output', 'Join', 'Data Source', 'Multi-Join'],
  // Nomi tecnici: linguaggi, protocolli, formati, prodotti.
  technical: [
    'SQL', 'API', 'REST', 'ArcGIS', 'CSV', 'GeoJSON', 'XLSX', 'JSON', 'WKT', 'CRS', 'SRID',
    'FTP', 'S3', 'SharePoint', 'URL', 'ID', 'OpenAPI', 'HTTP', 'IMDF', 'UUID', 'MD5',
    // Terminologia ArcGIS: nomi dei suoi oggetti, usati cosi' anche in italiano.
    'Feature Service', 'layer', 'Token',
    // Parole entrate nell'italiano tecnico, usate nei nomi dei canvas.
    'canvas', 'input', 'query', 'endpoint', 'cache', 'file', 'pivot',
  ],
});

/*
 * Entita'. `formerNames`: nomi delle versioni precedenti, riconosciuti come
 * titoli generati (non testo dell'utente) quando sono rimasti congelati in
 * `data.title` (regola 7).
 */
const T = {
  // ── Tipi di nodo ────────────────────────────────────────────────────────
  table: { name: 'Tabella', icon: ICON.table, description: 'Una tabella o una vista del database' },
  view: { name: 'Vista', icon: ICON.view, description: 'Una vista del database' },
  feature_layer: { name: 'Layer ArcGIS', icon: ICON.arcgis_layer, description: 'Un layer di un Feature Service ArcGIS' },
  project_source: { name: 'Progetto sorgente', icon: ICON.project_result, description: 'Il risultato di un altro progetto' },
  file_source: { name: 'File', icon: ICON.file, description: 'Un file caricato nella gestione file' },
  datasource: {
    name: 'Data Source', icon: ICON.datasource, description: 'File da SharePoint, FTP o cartella locale',
    formerNames: ['DataSource', 'DS'],
  },
  datasource_write: { name: 'Scrittura su Data Source', icon: ICON.datasource, description: "Scrive l'Output su un Data Source" },
  static_values: { name: 'Valori statici', icon: ICON.static_values, description: 'Valori fissi scritti a mano', formerNames: ['Valori Statici'] },
  runtime_input: { name: 'Input runtime', icon: ICON.runtime_input, description: 'Un valore chiesto quando il progetto viene eseguito' },
  transform: { name: 'Operazione', icon: ICON.operation, description: "Un'operazione sui dati", formerNames: ['Trasformazione'] },
  multi_join: {
    name: 'Multi-Join', icon: ICON.join, description: 'Unisce più sorgenti su chiavi comuni',
    formerNames: ['Unione', 'Multi Join', 'MultiJoin'],
  },
  api_enrich: { name: 'Arricchimento API', icon: ICON.api, description: 'Chiama una API per ogni riga' },
  output: { name: 'Output', icon: ICON.output, description: 'Il file finale del flusso' },
  load_db: {
    name: 'Carica su database', icon: ICON.load_db, description: "Scrive l'Output in una tabella o su un servizio REST",
    formerNames: ['Carica DB', 'CARICA DB', 'CARICA'],
  },
  load_arcgis: {
    name: 'Carica su ArcGIS', icon: ICON.load_arcgis, description: "Scrive l'Output su un layer ArcGIS",
    formerNames: ['Carica ArcGIS', 'CARICA ArcGIS'],
  },
  note: { name: 'Nota', icon: ICON.note, description: 'Un appunto libero sul canvas' },
  join: { name: 'Join', icon: ICON.join, description: 'Unisce due tabelle su colonne chiave' },
  concat: { name: 'Concatena', icon: ICON.concat, description: 'Accoda le righe di più tabelle' },
  lookup: { name: 'Mappa valori', icon: ICON.lookup, description: 'Ricodifica i valori di una colonna' },

  // ── Connettori di Arricchisci (palette e menu) ──────────────────────────
  connector_template: { name: 'Arricchimento da modello', icon: ICON.template, description: 'Una connessione creata da un modello di API' },
  connector_manual: { name: 'API manuale', icon: ICON.manual, description: 'Connettore configurato a mano' },
  connector_openapi: { name: 'API OpenAPI', icon: ICON.openapi, description: 'Da uno schema OpenAPI importato' },
  connector_rest_file: { name: 'Processore file', icon: ICON.file_processor, description: 'Servizio REST che riceve e restituisce file' },

  // ── Azioni dell'editor ──────────────────────────────────────────────────
  run: { name: 'Esegui', icon: ICON.run, description: 'Salva, controlla ed esegui', busyName: 'In esecuzione…' },
  before_run: { name: 'Prima di avviare', icon: ICON.before_run, description: 'Input, stima e conferma prima di eseguire' },
  fill_values: { name: 'Riempi con i valori presenti', icon: ICON.fill_values, description: "Propone come righe da tradurre i valori che la colonna ha nei dati in ingresso" },
  preview: { name: 'Anteprima', icon: ICON.preview, description: 'Mostra le prime righe del risultato' },
  validate: { name: 'Valida', icon: ICON.validate, description: 'Controlla il canvas prima di eseguire' },
  save: { name: 'Salva', icon: ICON.save, description: `Salva (${shortcutText('save')})`, busyName: 'Salvataggio…' },
  versions: { name: 'Versioni', icon: ICON.versions, description: 'Versioni salvate del canvas' },
  run_history: { name: 'Cronologia esecuzioni', icon: ICON.run_history, description: 'Le esecuzioni passate di questo passo' },
  connections: { name: 'Connessioni', icon: ICON.connection, description: 'Gestione delle connessioni ai database' },
  add_step: { name: 'Passo', icon: ICON.add, description: `Aggiungi un passo (${shortcutText('add_step')})` },
  delete_step: { name: 'Elimina passo', icon: ICON.delete, description: 'Elimina il passo e i suoi collegamenti' },
  schedule: { name: 'Pianificazione', icon: ICON.schedule, description: 'Esecuzioni automatiche programmate' },
  upload_file: { name: 'Carica un file', icon: ICON.file, description: 'Carica un file e aggiungilo al canvas senza uscire dall\'editor' },
};

export const TERMS = Object.freeze(T);

/** Tipo di nodo di React Flow (alias compresi) -> entita'. */
export const NODE_TYPE_TERMS = Object.freeze({
  tableNode: 'table',
  featureLayerNode: 'feature_layer',
  project_source: 'project_source',
  projectSource: 'project_source',
  file_source: 'file_source',
  fileSource: 'file_source',
  datasourceNode: 'datasource',
  datasource_node: 'datasource',
  staticValues: 'static_values',
  project_input: 'runtime_input',
  transform: 'transform',
  multi_join: 'multi_join',
  api_enrich: 'api_enrich',
  resultNode: 'output',
  result: 'output',
  carica: 'load_db',
  carica_arcgis: 'load_arcgis',
  caricaArcgis: 'load_arcgis',
  comment: 'note',
});

/** Entita' per chiave; un nome assente e' un errore di programmazione. */
export function term(key) {
  const t = T[key];
  if (!t) throw new Error(`[glossary] entita' non dichiarata: ${key}`);
  return t;
}

/** Nome dell'entita'. */
export const termName = (key) => term(key).name;

/*
 * Concetti dell'app (UI-4, U22): per ognuno il termine italiano scelto e
 * quelli che sostituisce. I sostituiti sono vietati nel testo visibile di
 * TUTTA l'app, pagine di sistema e /run comprese (gli editor hanno in piu'
 * l'elenco lungo del loro test): tests/shared/terms.contract.test.js.
 * Prima la stessa cosa era progetto, flusso, catena o pipeline a seconda
 * della pagina.
 */
export const CONCEPTS = Object.freeze({
  progetto: { term: 'progetto', replaces: ['flusso', 'flussi', 'catena', 'catene', 'pipeline', 'workflow'] },
  passo: { term: 'passo', replaces: ['step', 'steps'] },
  esecuzione: { term: 'esecuzione', replaces: ['run', 'runs', 'job', 'jobs', 'task', 'tasks'] },
  pianificazione: { term: 'pianificazione', replaces: ['schedule', 'scheduler', 'schedulazione', 'schedulato', 'schedulati'] },
  caricare: { term: 'carica', replaces: ['upload'] },
  scaricare: { term: 'scarica', replaces: ['download'] },
  pulizia: { term: 'pulizia', replaces: ['cleanup'] },
  anteprima: { term: 'anteprima', replaces: ['preview'] },
  modello: { term: 'modello', replaces: ['template', 'templates'] },
  tabella: { term: 'tabella', replaces: ['dataset'] },
  arricchimento: { term: 'arricchimento', replaces: ['enrichment'] },
  blocco: { term: 'a blocchi', replaces: ['batch'] },
  // «Output» e' solo il passo finale (sempre maiuscolo); il resto e' il risultato.
  risultato: { term: 'risultato', replaces: [], exactCase: ['Output'] },
});

/** Tipi di input runtime (pannello dell'input e riepiloghi): un solo nome. */
export const INPUT_KIND_LABELS = Object.freeze({
  file: 'File tabellare',
  text: 'Testo',
  integer: 'Intero',
  number: 'Numero',
  boolean: 'Sì / No',
  date: 'Data',
  datetime: 'Data e ora',
  select: 'Scelta',
  connection: 'Set di connessioni',
  current_user: 'Utente che esegue',
});
export const inputKindLabel = (kind) => INPUT_KIND_LABELS[String(kind || 'file').toLowerCase()] || String(kind);


/**
 * Voce del menu "⋯" di un editor: nome, icona e descrizione dal glossario.
 * `props` porta solo il comportamento (onClick, disabled, hidden).
 */
export function menuEntry(key, props = {}) {
  const t = term(key);
  return { key, icon: t.icon, label: t.name, title: t.description, ...props };
}

/** Voce di una palette: nome, icona e descrizione dal glossario. */
export function paletteEntry(key, props = {}) {
  const t = term(key);
  return { id: key, kind: key, label: t.name, icon: t.icon, desc: t.description, ...props };
}

/** Avvisi comuni dei tre editor sui passi unici per progetto. */
export const onlyOneMessage = (key) => `Puoi avere solo un passo «${termName(key)}» per progetto`;
export const addFirstMessage = (key) => `Aggiungi prima un passo «${termName(key)}»`;

/*
 * Archi verso l'Output e i caricamenti (`data.join_type`): l'etichetta
 * mostrata sul canvas viene dal glossario anche per gli archi salvati con
 * un nome vecchio ("Carica DB", "output"). La applica EditorShell.
 */
const FLOW_EDGE_TERMS = Object.freeze({ output: 'output', carica: 'load_db', carica_arcgis: 'load_arcgis' });

/** Etichetta di un arco di flusso (null se l'arco non e' di flusso). */
export function flowEdgeLabel(joinType) {
  const key = FLOW_EDGE_TERMS[joinType];
  return key ? T[key].name : null;
}

const labelledEdges = new WeakMap();

/** Archi con l'etichetta del glossario; stessa identita' se non cambia nulla. */
export function withFlowEdgeLabels(edges) {
  if (!Array.isArray(edges)) return edges;
  let changed = false;
  const out = edges.map((e) => {
    const label = flowEdgeLabel(e?.data?.join_type);
    if (!label || e.label === label) return e;
    changed = true;
    if (!labelledEdges.has(e)) labelledEdges.set(e, { ...e, label });
    return labelledEdges.get(e);
  });
  return changed ? out : edges;
}

/*
 * Catalogo delle operazioni Manipola (backend transforms_registry.json):
 * sovrascritture per chiave operazione dove il catalogo usa termini
 * inglesi. `params`: etichette dei parametri e delle loro opzioni
 * (`options`, per valore). Le voci da allineare nel backend sono queste.
 */
export const TRANSFORM_TERMS = Object.freeze({
  concat: { name: 'Concatena', description: 'Accoda le righe di più tabelle, una sotto l’altra' },
  aggregate: { name: 'Raggruppa' },
  lookup: {
    name: 'Mappa valori',
    params: { default: { label: 'Valore predefinito' }, unmatched: { options: { value: 'Valore predefinito' } } },
  },
  conditional: {
    name: 'Condizionale',
    description: 'Crea una colonna con logica se/allora su condizioni multiple',
    params: { default_value: { label: 'Valore predefinito' } },
  },
  sjoin: { name: 'Join spaziale' },
  geo_buffer: { name: 'Area di rispetto' },
  geo_clip: { name: 'Ritaglia' },
  geo_convex_hull: { description: "Calcola l'inviluppo convesso delle geometrie" },
  geo_envelope: { name: 'Rettangolo di ingombro' },
  geo_bounds_extractor: { name: 'Estrai rettangolo di ingombro' },
  geo_nearest: { name: 'Più vicino' },
  geo_boundary: { name: 'Contorno' },
  geo_point_on_surface: { description: "Calcola un punto garantito all'interno del poligono (per le etichette)" },
  geo_overlay: {
    name: 'Sovrapposizione poligoni',
    description: 'Sovrappone due layer poligonali combinando geometrie e attributi (intersezione, unione, identità)',
    params: { how: { label: 'Tipo di sovrapposizione' } },
  },
  melt: { name: 'Colonne in righe', description: 'Trasforma colonne in righe (inverso della tabella pivot): da formato largo a lungo' },
  bin: { name: 'Classi e intervalli' },
  string_extract: {
    name: 'Estrai con espressione regolare',
    description: 'Estrae testo da una colonna con un’espressione regolare: utile per indirizzi, codici, ecc.',
  },
  cross_join: { name: 'Prodotto cartesiano' },
  string_pad: { name: 'Completa a lunghezza fissa' },
  dedup_advanced: { name: 'Rimuovi duplicati con priorità' },
  md5_hash: { description: 'Crea una colonna con l’impronta MD5 di una o più colonne (utile per rilevare le modifiche)' },
  statistics: { description: 'Calcola statistiche su una colonna numerica (conteggio, minimo, massimo, media, mediana, deviazione standard, percentili)' },
  window_function: { description: 'Calcoli su finestre: classifica, somma cumulativa, valore precedente o successivo' },
  pivot: { params: { mapping: { label: 'Mappatura delle colonne in uscita' } } },
  text_normalize: {
    params: {
      operations: { options: { trim: 'Rimuovi spazi ai bordi', full: 'Completa (spazi, minuscole, accenti)' } },
    },
  },
});

/*
 * Espressioni ricorrenti del catalogo, sostituite in TUTTE le operazioni
 * (nomi, descrizioni, etichette e opzioni dei parametri): "output" come
 * parola comune e' "in uscita" (Output e' solo il passo finale), "join"
 * e' il nome dell'entita' Join, "dataset" e' la tabella.
 */
const CATALOG_PHRASES = Object.freeze([
  [/\b([Cc])olonna output\b/g, '$1olonna in uscita'],
  [/\b([Cc])olonne output\b/g, '$1olonne in uscita'],
  [/\bCRS output\b/g, 'CRS in uscita'],
  [/\bTipo join\b/g, 'Tipo di Join'],
  [/\bdal dataset\b/g, 'dalla tabella'],
  [/\bnel dataset\b/g, 'nella tabella'],
  // Descrizioni dei parametri CRS delle operazioni geografiche.
  [/Override del CRS per calcolo (\w+)\. Vuoto = auto-UTM\./g, 'CRS metrico da usare per il calcolo ($1). Vuoto = UTM scelto in automatico.'],
  [/'same' mantiene il CRS input \(default\), 'metric' lascia il CRS metrico( usato per il calcolo)?( \(no roundtrip\))?\./g,
    "'same' mantiene il CRS di ingresso (predefinito), 'metric' lascia il CRS metrico usato per il calcolo."],
]);

/*
 * Descrizioni dei parametri del catalogo riscritte per intero (testi di
 * aiuto mostrati sotto il campo), dove le frasi comuni non bastano.
 */
export const PARAM_DESCRIPTIONS = Object.freeze({
  select_columns: { columns: 'Lista ordinata di colonne: nome, se tenerla (predefinito sì), etichetta facoltativa.' },
  filter: { conditions: 'Lista di condizioni (colonna, operatore, valore); se presente sostituisce il filtro singolo. Lista vuota: nessun filtro.' },
  geo_buffer: { metric_crs: 'CRS metrico da usare per il calcolo. Vuoto = UTM scelto in automatico. Utile per tabelle su più zone (es. EPSG:3035 per LAEA Europa).' },
  md5_hash: { normalize: 'Toglie gli spazi ai bordi e porta in minuscolo prima del calcolo (consigliato per rilevare le modifiche).' },
  lookup: { unmatched: "Mantieni l'originale, null oppure un valore predefinito." },
  flatten_json: { output_columns: "Chiavi JSON attese, usate per prevedere lo schema nel canvas. Se vuota, lo schema previsto resta uguale all'ingresso. L'esecuzione estrae comunque tutte le chiavi presenti nei dati." },
  geo_overlay: { keep_geom_type: 'Rimuovi frammenti di tipo diverso (es. linee generate dalla sovrapposizione di poligoni).' },
  string_extract: {
    pattern: 'Espressione regolare con gruppo di cattura, es: (\\d{5}) per CAP, (?P<via>.+),\\s*(?P<civico>\\d+)',
    output_column: "Vuoto = colonna_extracted (ignorato se l'espressione regolare ha gruppi con nome).",
  },
  transpose: { output_columns: "Nomi delle nuove colonne attese dopo la trasposizione (i valori della colonna identificativa), usati per prevedere lo schema nel canvas. Se vuota, lo schema previsto resta uguale all'ingresso. L'esecuzione traspone comunque i dati effettivi." },
});

const rephrase = (text) => (typeof text === 'string'
  ? CATALOG_PHRASES.reduce((t, [re, to]) => t.replace(re, to), text)
  : text);

/**
 * Definizione di un'operazione con i nomi del glossario: le espressioni
 * comuni (CATALOG_PHRASES) e poi le sovrascritture per chiave
 * (TRANSFORM_TERMS). `catalog_name` conserva il nome del backend se e'
 * cambiato (titoli congelati, ricerca nella palette).
 */
export function applyTransformTerms(op, def) {
  if (!def || typeof def !== 'object') return def;
  const over = TRANSFORM_TERMS[op] || {};
  const out = { ...def, name: rephrase(def.name), description: rephrase(def.description) };
  if (over.name) out.name = over.name;
  if (over.description) out.description = over.description;
  if (out.name !== def.name) out.catalog_name = def.name;
  if (Array.isArray(def.parameters)) {
    out.parameters = def.parameters.map(p => {
      if (!p || typeof p !== 'object') return p;
      const po = (over.params || {})[p.id] || {};
      const next = { ...p, label: po.label || rephrase(p.label), description: po.description || PARAM_DESCRIPTIONS[op]?.[p.id] || rephrase(p.description) };
      if (Array.isArray(p.options)) {
        next.options = p.options.map(o => (o && typeof o === 'object'
          ? { ...o, label: (po.options || {})[o.value] || rephrase(o.label) } : o));
      }
      return next;
    });
  }
  return out;
}
