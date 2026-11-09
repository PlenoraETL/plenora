/**
 * projectChains.js — le catene della home (09/11/2026, mockup approvato
 * dall'utente). Funzioni pure sopra la risposta di GET /dashboard/graph:
 *
 *  - buildGraph: nodi (progetti, Eseguibili, origini: connessioni, file, Data
 *    Source) e collegamenti diretti (`ups`, `downs`);
 *  - buildChains: una catena per componente connesso del grafo dei
 *    progetti e degli Eseguibili. Le origini NON entrano nel calcolo dei
 *    componenti (una connessione usata da tutti farebbe una catena sola):
 *    si mostrano nella prima colonna di ogni catena che le usa. Titolo = il
 *    nodo piu' a valle (il finale con il cammino piu' lungo; a parita', il
 *    primo per nome). I progetti isolati (nessuno li usa, non pubblicati,
 *    non leggono altri progetti) vanno nella catena finale «Non usati da
 *    altri progetti»;
 *  - lineage: a monte e a valle (transitivi) di un nodo, per l'evidenza;
 *  - WATCH_RULES: le regole «Da guardare», UNA tabella dichiarativa; ogni
 *    regola dice su che nodi vale, di quali parti della risposta ha bisogno
 *    (`needs`: con una parte non disponibile la regola tace, mai un avviso
 *    falso) e il testo dell'avviso;
 *  - orderChains / filterChains / chainCounters: ordine (prima le catene con
 *    qualcosa da guardare), filtri e contatori del pannello.
 *
 * Colonne (scelta dichiarata): Connessione -> Estrai -> Manipola e
 * Arricchisci -> Pubblicato. Arricchisci sta con Manipola: entrambi
 * trasformano dati letti da altri progetti, e una colonna in piu' lascerebbe
 * vuota quasi ogni riga; il tipo resta scritto sulla scheda.
 */
import { formatDayMonth, parseServerDate } from './format';
import { projectType } from './projectTypes';

export const COLUMNS = Object.freeze([
  { key: 'origin', label: 'Connessione' },
  { key: 'estrai', label: 'Estrai' },
  { key: 'transform', label: 'Manipola e Arricchisci' },
  { key: 'published', label: 'Pubblicato' },
]);

/** Oltre questo numero di schede una colonna si richiude («9 Estrai»). */
export const COLLAPSE_AT = 5;

const ISOLATED_CHAIN_ID = 'chain:isolated';

// Etichetta del tipo di ogni nodo (badge della scheda).
const ORIGIN_LABELS = Object.freeze({ connection: 'Connessione', file: 'File', datasource: 'Data Source' });
export function typeLabel(node) {
  if (!node) return '';
  if (node.kind === 'eseguibile') return 'Eseguibile';
  if (node.kind === 'origin') return ORIGIN_LABELS[node.type] || 'Connessione';
  return projectType(node.type).label;
}

// Destinazioni di Carica (nomi del glossario degli editor).
const LOAD_LABELS = Object.freeze({
  db: 'Carica su database',
  rest: 'Carica su REST',
  arcgis: 'Carica su ArcGIS',
  datasource: 'Scrittura su Data Source',
});

const collator = new Intl.Collator('it', { sensitivity: 'base', numeric: true });
export const byName = (a, b) => collator.compare(a.name || '', b.name || '') || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);

const timeOf = (value) => parseServerDate(value)?.getTime() ?? NaN;
// Conteggi del server (interi): un valore assente o non numerico vale 0.
const count = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

const projectKey = (id) => `p:${id}`;
const eseguibileKey = (id) => `x:${id}`;

/**
 * Il grafo: { nodes: Map<key, node>, ups: Map<key, key[]>, downs: Map<key, key[]>, unavailable: Set }.
 * `connections`: Map id -> { name, kind } (liste del client); `fileNames`: Map id -> nome.
 */
export function buildGraph(raw, { connections = new Map(), fileNames = new Map() } = {}) {
  const nodes = new Map();
  const ups = new Map();
  const downs = new Map();
  const unavailable = new Set(Array.isArray(raw?.unavailable) ? raw.unavailable : []);
  const link = (from, to) => {
    if (!nodes.has(from) || !nodes.has(to) || from === to) return;
    const u = ups.get(to) || [];
    if (u.includes(from)) return;
    u.push(from);
    ups.set(to, u);
    downs.set(from, [...(downs.get(from) || []), to]);
  };
  const origin = (type, id, name, extra = {}) => {
    const key = `${type[0]}:${id}`;
    if (!nodes.has(key)) nodes.set(key, { key, kind: 'origin', type, id, name, ...extra });
    return key;
  };

  const projects = Array.isArray(raw?.projects) ? raw.projects : [];
  const eseguibili = Array.isArray(raw?.eseguibili) ? raw.eseguibili : [];
  for (const p of projects) {
    nodes.set(projectKey(p.id), { key: projectKey(p.id), kind: 'project', type: p.project_type || 'estrai', id: p.id, name: p.name || '', raw: p });
  }
  for (const e of eseguibili) {
    nodes.set(eseguibileKey(e.id), { key: eseguibileKey(e.id), kind: 'eseguibile', type: 'eseguibile', id: e.id, name: e.name || '', raw: e });
  }
  const connectionOrigin = (id, kind) => {
    const known = connections.get(id);
    return origin('connection', id, known?.name || 'Connessione non disponibile', { connectionKind: known?.kind || kind || 'db', missing: !known });
  };
  for (const p of projects) {
    const key = projectKey(p.id);
    const connIds = new Map((p.connections || []).map((c) => [c.id, c.kind]));
    if (p.default_connection_id && !connIds.has(p.default_connection_id)) connIds.set(p.default_connection_id, null);
    for (const [id, kind] of connIds) link(connectionOrigin(id, kind), key);
    for (const id of p.file_source_ids || []) {
      link(origin('file', id, fileNames.get(id) || 'File non disponibile', { missing: !fileNames.has(id) }), key);
    }
    for (const ds of p.datasources || []) link(origin('datasource', ds.id, ds.name || 'Data Source'), key);
    for (const id of p.source_project_ids || []) link(projectKey(id), key);
  }
  for (const e of eseguibili) {
    if (e.source_project_id) link(projectKey(e.source_project_id), eseguibileKey(e.id));
  }
  return { nodes, ups, downs, unavailable };
}

const isMember = (node) => node && node.kind !== 'origin';
const directUps = (graph, key) => graph.ups.get(key) || [];
const directDowns = (graph, key) => graph.downs.get(key) || [];

/** Tutti i nodi raggiunti seguendo `next` (senza il nodo di partenza). */
function walk(start, next) {
  const seen = new Set();
  const stack = [start];
  while (stack.length) {
    const x = stack.pop();
    for (const y of next(x)) {
      if (y !== start && !seen.has(y)) { seen.add(y); stack.push(y); }
    }
  }
  return seen;
}

/** A monte (origini comprese) e a valle di un nodo, transitivi. */
export function lineage(graph, key) {
  if (!key || !graph.nodes.has(key)) return { up: new Set(), down: new Set() };
  return { up: walk(key, (k) => directUps(graph, k)), down: walk(key, (k) => directDowns(graph, k)) };
}

/** 'sel' | 'up' | 'down' | 'none' rispetto alla selezione (null = nessuna). */
export function relationOf(key, selected, lin) {
  if (!selected) return null;
  if (key === selected) return 'sel';
  if (lin.up.has(key)) return 'up';
  if (lin.down.has(key)) return 'down';
  return 'none';
}

const columnOf = (node) => (node.kind === 'origin' ? 'origin'
  : node.kind === 'eseguibile' ? 'published'
    : node.type === 'estrai' ? 'estrai' : 'transform');

function chainColumns(graph, members) {
  const cols = { origin: [], estrai: [], transform: [], published: [] };
  const origins = new Set();
  for (const m of members) {
    cols[columnOf(m)].push(m);
    for (const u of directUps(graph, m.key)) if (graph.nodes.get(u)?.kind === 'origin') origins.add(u);
  }
  cols.origin = [...origins].map((k) => graph.nodes.get(k));
  for (const k of Object.keys(cols)) cols[k].sort(byName);
  return cols;
}

function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

function chainDesc(cols, members) {
  const count = (type) => members.filter((m) => m.kind === 'project' && m.type === type).length;
  const parts = [];
  for (const type of ['estrai', 'manipola', 'arricchisci']) {
    const n = count(type);
    if (n) parts.push(`${n} ${projectType(type).label}`);
  }
  if (cols.published.length) parts.push(plural(cols.published.length, 'pubblicato', 'pubblicati'));
  return parts.join(' · ');
}

/** Il nodo piu' a valle: finale con il cammino piu' lungo, poi il primo per nome. */
function chainTitleNode(graph, members) {
  const inside = new Set(members.map((m) => m.key));
  const depth = new Map();
  const depthOf = (key, visiting = new Set()) => {
    if (depth.has(key)) return depth.get(key);
    if (visiting.has(key)) return 0; // ciclo (il server li rifiuta): non conta
    visiting.add(key);
    const parents = directUps(graph, key).filter((u) => inside.has(u));
    const d = parents.length ? 1 + Math.max(...parents.map((u) => depthOf(u, visiting))) : 0;
    visiting.delete(key);
    depth.set(key, d);
    return d;
  };
  const sinks = members.filter((m) => !directDowns(graph, m.key).some((d) => inside.has(d)));
  const pool = sinks.length ? sinks : members;
  const max = Math.max(...pool.map((m) => depthOf(m.key)));
  return pool.filter((m) => depthOf(m.key) === max).sort(byName)[0];
}

/**
 * Le catene: [{ id, title, titleKey, desc, columns, nodes (tutte le schede),
 * isolated }]. L'ordine e' quello del nome; orderChains mette davanti quelle
 * con qualcosa da guardare.
 */
export function buildChains(graph) {
  const parent = new Map();
  const find = (k) => {
    let r = k;
    while (parent.get(r) !== r) r = parent.get(r);
    let x = k;
    while (parent.get(x) !== r) { const n = parent.get(x); parent.set(x, r); x = n; }
    return r;
  };
  const members = [...graph.nodes.values()].filter(isMember);
  for (const m of members) parent.set(m.key, m.key);
  for (const m of members) {
    for (const d of directDowns(graph, m.key)) {
      if (!parent.has(d)) continue;
      const a = find(m.key); const b = find(d);
      if (a !== b) parent.set(a, b);
    }
  }
  const groups = new Map();
  for (const m of members) {
    const r = find(m.key);
    groups.set(r, [...(groups.get(r) || []), m]);
  }
  const chains = [];
  const isolated = [];
  for (const group of groups.values()) {
    if (group.length === 1 && group[0].kind === 'project') { isolated.push(group[0]); continue; }
    const columns = chainColumns(graph, group);
    const head = chainTitleNode(graph, group);
    chains.push({
      id: `chain:${head.key}`, title: head.name, titleKey: head.key,
      desc: chainDesc(columns, group), columns,
      nodes: [...columns.origin, ...columns.estrai, ...columns.transform, ...columns.published],
      isolated: false,
    });
  }
  chains.sort((a, b) => collator.compare(a.title, b.title) || (a.id < b.id ? -1 : 1));
  if (isolated.length) {
    const columns = chainColumns(graph, isolated);
    chains.push({
      id: ISOLATED_CHAIN_ID, title: 'Non usati da altri progetti', titleKey: null,
      desc: 'Nessun progetto li legge e non sono pubblicati', columns,
      nodes: [...columns.origin, ...columns.estrai, ...columns.transform, ...columns.published],
      isolated: true,
    });
  }
  return chains;
}

// ── «Da guardare» ─────────────────────────────────────────────────────────

const runAt = (node) => node.raw?.last_run?.at || null;
const nameKey = (name) => String(name || '').trim().toLocaleLowerCase('it');
const quoted = (names, max = 2) => {
  const shown = names.slice(0, max).map((n) => `«${n}»`).join(', ');
  return names.length > max ? `${shown} e altri ${names.length - max}` : shown;
};
const projectUps = (ctx, node) => directUps(ctx.graph, node.key).map((k) => ctx.graph.nodes.get(k)).filter((n) => n?.kind === 'project');

/**
 * Regole «Da guardare»: una riga per regola.
 *  - kinds: su quali nodi vale; needs: parti della risposta senza le quali
 *    tace (`unavailable` del server); enabled: false = dichiarata ma spenta;
 *  - check(node, ctx) -> testo dell'avviso o null.
 */
export const WATCH_RULES = Object.freeze([
  {
    id: 'stale_sources',
    title: 'Dati vecchi',
    kinds: ['project'],
    needs: ['runs', 'links'],
    enabled: true,
    // Una sorgente ha un'esecuzione riuscita piu' recente dell'ultima
    // esecuzione del progetto che la legge.
    check(node, ctx) {
      const at = timeOf(runAt(node));
      if (!Number.isFinite(at) || node.raw?.last_run?.status === 'running') return null;
      const newer = projectUps(ctx, node).filter((s) => timeOf(s.raw?.last_success_at) > at);
      if (!newer.length) return null;
      const when = formatDayMonth(runAt(node));
      if (newer.length === 1) {
        return `«${newer[0].name}» è stato rieseguito il ${formatDayMonth(newer[0].raw.last_success_at)}, dopo la sua ultima esecuzione (${when}): i dati possono essere vecchi`;
      }
      return `${newer.length} sorgenti sono state rieseguite dopo la sua ultima esecuzione (${when}): i dati possono essere vecchi`;
    },
  },
  {
    id: 'eseguibile_without_input',
    title: 'Eseguibile senza input',
    kinds: ['eseguibile'],
    needs: ['eseguibili'],
    enabled: true,
    // Avviso neutro: puo' essere voluto, ma chi lo esegue vede tutto.
    check(node) {
      const e = node.raw || {};
      return e.has_manifest && count(e.inputs_count) === 0
        ? 'Nessun input: chi lo esegue vede tutti i dati del progetto'
        : null;
    },
  },
  {
    id: 'duplicate_name',
    title: 'Nome doppio',
    kinds: ['project'],
    needs: [],
    enabled: true,
    check(node, ctx) {
      const others = (ctx.byName.get(nameKey(node.name)) || []).filter((o) => o.key !== node.key);
      if (!others.length) return null;
      if (others.length > 1) return `Stesso nome di altri ${others.length} progetti`;
      const [o] = others;
      const label = projectType(o.type).label;
      if (directUps(ctx.graph, node.key).includes(o.key)) return `Stesso nome del progetto ${label} da cui legge`;
      if (directDowns(ctx.graph, node.key).includes(o.key)) return `Stesso nome del progetto ${label} che lo legge`;
      return `Stesso nome di un altro progetto ${label}`;
    },
  },
  {
    id: 'never_run_or_failed',
    title: 'Mai eseguito o non riuscito',
    kinds: ['project'],
    needs: ['runs'],
    enabled: true,
    check(node) {
      const run = node.raw?.last_run;
      if (!run) return 'Mai eseguito';
      if (run.status === 'failure' || run.status === 'error') return `Ultima esecuzione non riuscita (${formatDayMonth(run.at)})`;
      return null;
    },
  },
  {
    id: 'no_schedule_upstream',
    title: 'Nessuna pianificazione a monte',
    kinds: ['eseguibile'],
    needs: ['schedules', 'links', 'eseguibili'],
    enabled: true,
    // I progetti a monte del progetto pubblicato (quelli di cui legge i
    // risultati): se nessuno e' pianificato, i dati dell'Eseguibile si
    // aggiornano solo eseguendoli a mano. Un Eseguibile di un Estrai legge
    // il database a ogni esecuzione: niente a monte, la regola tace.
    check(node, ctx) {
      const source = projectUps(ctx, node)[0];
      if (!source) return null;
      const above = [...lineage(ctx.graph, source.key).up].map((k) => ctx.graph.nodes.get(k)).filter((n) => n?.kind === 'project');
      if (!above.length || above.some((p) => count(p.raw?.schedules_active) > 0)) return null;
      const names = above.sort(byName).map((p) => p.name);
      return `Nessuna pianificazione a monte: ${quoted(names)} ${names.length === 1 ? 'si aggiorna' : 'si aggiornano'} solo eseguendo a mano`;
    },
  },
  {
    id: 'changed_after_publish',
    title: 'Progetto cambiato dopo la pubblicazione',
    kinds: ['eseguibile'],
    needs: ['eseguibili'],
    // N1 (Eseguibile pubblicato che non corrisponde piu' al suo progetto):
    // GET /dashboard/graph porta il campo della lista degli Eseguibili
    // (eseguibili.db.SOURCE_CHANGED_SQL); null (non admin) = la regola tace.
    enabled: true,
    field: 'source_changed_since_publish',
    check(node) {
      return node.raw?.source_changed_since_publish
        ? 'Il progetto è cambiato dopo la pubblicazione: chi lo esegue usa ancora la versione pubblicata'
        : null;
    },
  },
]);

/**
 * Avvisi del grafo: { byKey: Map<key, [{ rule, text }]>, list: [{ key, rule, text }] }.
 * `rules` per i test (default: WATCH_RULES).
 */
export function watchFindings(graph, { rules = WATCH_RULES } = {}) {
  const byNameMap = new Map();
  for (const n of graph.nodes.values()) {
    if (n.kind !== 'project') continue;
    const k = nameKey(n.name);
    byNameMap.set(k, [...(byNameMap.get(k) || []), n]);
  }
  const ctx = { graph, byName: byNameMap };
  const active = rules.filter((r) => r.enabled && !(r.needs || []).some((p) => graph.unavailable.has(p)));
  const byKey = new Map();
  const list = [];
  const ordered = [...graph.nodes.values()].sort(byName);
  for (const rule of active) {
    for (const node of ordered) {
      if (!rule.kinds.includes(node.kind)) continue;
      const text = rule.check(node, ctx);
      if (!text) continue;
      const item = { key: node.key, rule, text };
      list.push(item);
      byKey.set(node.key, [...(byKey.get(node.key) || []), item]);
    }
  }
  return { byKey, list };
}

/** Prima le catene con qualcosa da guardare, poi per titolo; «Non usati» in fondo. */
export function orderChains(chains, findings) {
  const warned = (c) => c.nodes.some((n) => findings.byKey.has(n.key));
  return [...chains].sort((a, b) => (
    (a.isolated - b.isolated)
    || (warned(b) - warned(a))
    || collator.compare(a.title, b.title)
  ));
}

/** Testo cercato in una scheda: nome e tabelle lette o scritte (la connessione e' la sua scheda). */
const nodeSearchValues = (node) => [node.name, ...(node.raw?.tables || [])];


/**
 * Filtro («all» | «warn» | «pub» | «unused») e ricerca: `matches(valori)` e'
 * la regola della ricerca dell'app (hooks/useSearch); una catena resta se
 * una sua scheda corrisponde.
 */
export function filterChains(chains, { filter = 'all', matches = null, findings }) {
  return chains.filter((c) => {
    if (filter === 'warn' && !c.nodes.some((n) => findings.byKey.has(n.key))) return false;
    if (filter === 'pub' && !c.columns.published.length) return false;
    if (filter === 'unused' && !c.isolated) return false;
    return !matches || c.nodes.some((n) => matches(nodeSearchValues(n)));
  });
}

/** Contatori del pannello: progetti, pubblicati, pianificazioni attive (null = non disponibile), connessioni usate. */
export function chainCounters(graph) {
  const all = [...graph.nodes.values()];
  const projects = all.filter((n) => n.kind === 'project');
  return {
    projects: projects.length,
    published: graph.unavailable.has('eseguibili') ? null : all.filter((n) => n.kind === 'eseguibile').length,
    schedulesActive: graph.unavailable.has('schedules') ? null : projects.reduce((s, p) => s + count(p.raw?.schedules_active), 0),
    connectionsUsed: all.filter((n) => n.kind === 'origin' && n.type === 'connection' && directDowns(graph, n.key).length).length,
  };
}

/** Riga di stato di una scheda: ultima esecuzione, input, tipo di origine. */
export function nodeMeta(node, { runsUnavailable = false } = {}) {
  if (node.kind === 'origin') {
    if (node.type === 'connection') return node.connectionKind === 'arcgis' ? 'ArcGIS' : 'Database';
    return node.missing ? 'Non disponibile' : (node.type === 'file' ? 'File caricato' : 'File da SharePoint, FTP o cartella');
  }
  if (node.kind === 'eseguibile') {
    const e = node.raw || {};
    const version = `versione ${e.active_version || 1}`;
    if (!e.has_manifest) return `Versione ${e.active_version || 1}`;
    const n = count(e.inputs_count);
    return `${n === 0 ? 'Nessun input' : plural(n, 'input', 'input')} · ${version}`;
  }
  if (runsUnavailable) return 'Stato non disponibile';
  const run = node.raw?.last_run;
  if (!run) return 'Mai eseguito';
  const outcome = run.status === 'success' ? 'riuscita'
    : run.status === 'running' ? 'in corso'
      : (run.status === 'failure' || run.status === 'error') ? 'non riuscita' : (run.status || '');
  return `Ultima esecuzione ${formatDayMonth(run.at)}${outcome ? ` · ${outcome}` : ''}`;
}

/** Segni fissi di una scheda (Carica, pianificata). */
export function nodeTags(node) {
  if (node.kind !== 'project') return [];
  const tags = (node.raw?.loads || []).map((l) => LOAD_LABELS[l]).filter(Boolean);
  if (count(node.raw?.schedules_active) > 0) tags.push('Pianificato');
  if (node.raw?.canvas_corrupted) tags.push('Canvas illeggibile');
  return tags;
}
