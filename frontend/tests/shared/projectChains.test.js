/**
 * Home per catene (09/11/2026): costruzione delle catene, tabella «Da
 * guardare», evidenza a monte/a valle, filtri e contatori (utils/projectChains),
 * richiesta «Esegui» dalla home (utils/pendingRun).
 */
import { describe, it, expect } from 'vitest';
import {
  buildGraph, buildChains, lineage, relationOf, watchFindings, orderChains, filterChains,
  chainCounters, nodeMeta, nodeTags, WATCH_RULES, COLLAPSE_AT, chainTitleWrites,
} from '../../src/utils/projectChains';
import { invalidationsForWrite } from '../../src/utils/sharedInvalidations';
import { requestRunOnOpen, takeRunOnOpen } from '../../src/utils/pendingRun';
import { PRODUCTION_CONNECTIONS, productionGraph, SC8 } from '../fixtures/homeChainsGraph';

const graphOf = (raw) => buildGraph(raw, { connections: PRODUCTION_CONNECTIONS });
const prod = () => graphOf(productionGraph());
const titles = (chains) => chains.map((c) => c.title);
const keysOf = (nodes) => nodes.map((n) => n.key);

const P = (id, project_type = 'estrai', extra = {}) => ({
  id, name: id, project_type, connections: [], source_project_ids: [], file_source_ids: [], datasources: [],
  loads: [], tables: [], last_run: { status: 'success', at: '2026-11-01T08:00:00Z' }, last_success_at: '2026-11-01T08:00:00Z',
  schedules: 0, schedules_active: 0, ...extra,
});
const X = (id, source, extra = {}) => ({ id, name: id, project_type: 'manipola', source_project_id: source, inputs_count: 1, has_manifest: true, active_version: 1, ...extra });
const mini = (projects, eseguibili = [], unavailable = []) => graphOf({ projects, eseguibili, unavailable });

describe('catene: componenti, titoli, isolati, ordine', () => {
  it('produzione: 19 progetti, 2 pubblicati, 4 catene collegate e i non usati', () => {
    const graph = prod();
    const chains = buildChains(graph);
    expect([...graph.nodes.values()].filter((n) => n.kind === 'project')).toHaveLength(19);
    expect(titles(chains)).toEqual([
      'Copia di immobili attivi affittati/sfitti',
      'Copia di SC_scheda_manipola',
      'immobili attivi per istituto',
      'SC_A_patrimonio_unita_AMM',
      'Non usati da altri progetti',
    ]);
    const scheda = chains[1];
    expect(scheda.columns.estrai).toHaveLength(9);
    expect(keysOf(scheda.columns.transform)).toEqual(['p:cscm', 'p:scm']);
    expect(keysOf(scheda.columns.origin)).toEqual(['c:conn-1']);
    expect(scheda.desc).toBe('9 Estrai · 2 Manipola');
    const iapi = chains[2];
    expect(keysOf(iapi.columns.published)).toEqual(['x:ex-iapi']);
    expect(iapi.desc).toBe('1 Estrai · 1 Manipola · 1 pubblicato');
    const unused = chains.at(-1);
    expect(unused.isolated).toBe(true);
    expect(keysOf(unused.columns.estrai)).toEqual(['p:cat', 'p:tk']);
    expect(9).toBeGreaterThan(COLLAPSE_AT); // la colonna dei 9 Estrai si richiude
  });

  it('la connessione non entra nei componenti: due Estrai sulla stessa connessione restano separati', () => {
    const chains = buildChains(mini([P('a', 'estrai', { connections: [{ id: 'conn-1', kind: 'db' }] }), P('b', 'estrai', { default_connection_id: 'conn-1' })]));
    expect(chains).toHaveLength(1);
    expect(chains[0].isolated).toBe(true);
    expect(keysOf(chains[0].columns.estrai)).toEqual(['p:a', 'p:b']);
    expect(keysOf(chains[0].columns.origin)).toEqual(['c:conn-1']);
  });

  it('titolo: il finale con il cammino piu\' lungo; a parita\' il primo per nome', () => {
    const graph = mini([
      P('e'), P('m1', 'manipola', { source_project_ids: ['e'] }), P('m2', 'manipola', { source_project_ids: ['m1'] }),
      P('zz', 'manipola', { source_project_ids: ['e'] }),
    ]);
    expect(titles(buildChains(graph))).toEqual(['m2']);
    const tie = mini([P('e'), P('beta', 'manipola', { source_project_ids: ['e'] }), P('alfa', 'manipola', { source_project_ids: ['e'] })]);
    expect(titles(buildChains(tie))).toEqual(['alfa']);
  });

  it('isolato = non usato, non pubblicato e senza sorgenti tra i progetti; un Estrai pubblicato non lo e\'', () => {
    const chains = buildChains(mini([P('solo'), P('pub')], [X('x1', 'pub')]));
    expect(titles(chains)).toEqual(['x1', 'Non usati da altri progetti']);
    expect(keysOf(chains[1].nodes.filter((n) => n.kind === 'project'))).toEqual(['p:solo']);
  });

  it('una sorgente non visibile non crea collegamenti ne\' nodi', () => {
    const graph = mini([P('m', 'manipola', { source_project_ids: ['altro-gruppo'] })], [X('x', 'nascosto')]);
    expect(graph.nodes.has('p:altro-gruppo')).toBe(false);
    expect(titles(buildChains(graph))).toEqual(['x', 'Non usati da altri progetti']);
  });

  it('ordine: prima le catene con qualcosa da guardare, poi per titolo; i non usati in fondo', () => {
    const graph = prod();
    const ordered = orderChains(buildChains(graph), watchFindings(graph));
    expect(titles(ordered)).toEqual([
      'Copia di immobili attivi affittati/sfitti',
      'Copia di SC_scheda_manipola',
      'immobili attivi per istituto',
      'SC_A_patrimonio_unita_AMM',
      'Non usati da altri progetti',
    ]);
    // Una catena senza avvisi che per nome verrebbe prima resta dopo.
    const g2 = mini([P('a'), P('am', 'manipola', { source_project_ids: ['a'] }), P('z', 'estrai', { last_run: null }), P('zm', 'manipola', { source_project_ids: ['z'] })]);
    expect(titles(orderChains(buildChains(g2), watchFindings(g2)))).toEqual(['zm', 'am']);
  });
});

// ── «Da guardare»: ogni regola un caso che scatta e uno che no ─────────────
const OLD = '2026-09-10T08:00:00Z';
const NEW = '2026-09-29T08:00:00Z';
const CASES = {
  stale_sources: {
    fires: mini([P('s', 'estrai', { last_success_at: NEW }), P('m', 'manipola', { source_project_ids: ['s'], last_run: { status: 'success', at: OLD } })]),
    silent: mini([P('s', 'estrai', { last_success_at: OLD }), P('m', 'manipola', { source_project_ids: ['s'], last_run: { status: 'success', at: NEW } })]),
    key: 'p:m',
    text: /«s» è stato rieseguito il .*dopo la sua ultima esecuzione/,
  },
  eseguibile_without_input: {
    fires: mini([P('p')], [X('x', 'p', { inputs_count: 0 })]),
    silent: mini([P('p')], [X('x', 'p', { inputs_count: 1 })]),
    key: 'x:x',
    text: /^Nessun input: chi lo esegue vede tutti i dati del progetto$/,
  },
  duplicate_name: {
    fires: mini([P('e', 'estrai', { name: 'Stesso' }), P('m', 'manipola', { name: 'stesso ', source_project_ids: ['e'] })]),
    silent: mini([P('e', 'estrai', { name: 'Uno' }), P('m', 'manipola', { name: 'Due', source_project_ids: ['e'] })]),
    key: 'p:m',
    text: /^Stesso nome del progetto Estrai da cui legge$/,
  },
  never_run_or_failed: {
    fires: mini([P('a', 'estrai', { last_run: null })]),
    silent: mini([P('a')]),
    key: 'p:a',
    text: /^Mai eseguito$/,
  },
  no_schedule_upstream: {
    fires: mini([P('e'), P('m', 'manipola', { source_project_ids: ['e'] })], [X('x', 'm')]),
    silent: mini([P('e', 'estrai', { schedules: 1, schedules_active: 1 }), P('m', 'manipola', { source_project_ids: ['e'] })], [X('x', 'm')]),
    key: 'x:x',
    text: /^Nessuna pianificazione a monte: «e» si aggiorna solo eseguendo a mano$/,
  },
  changed_after_publish: {
    fires: mini([P('p')], [X('x', 'p', { source_changed_since_publish: true })]),
    silent: mini([P('p')], [X('x', 'p', { source_changed_since_publish: false })]),
    key: 'x:x',
    text: /cambiato dopo la pubblicazione/,
  },
};

const enabled = (rule) => [{ ...rule, enabled: true }];

describe('«Da guardare»: tabella WATCH_RULES', () => {
  it('ogni regola della tabella ha il suo caso (enumerazione)', () => {
    expect(Object.keys(CASES).sort()).toEqual(WATCH_RULES.map((r) => r.id).sort());
    for (const r of WATCH_RULES) {
      expect(typeof r.check).toBe('function');
      expect(r.kinds.length).toBeGreaterThan(0);
      expect(Array.isArray(r.needs)).toBe(true);
    }
  });

  it.each(WATCH_RULES.map((r) => [r.id, r]))('%s: scatta nel suo caso e tace nell\'altro', (id, rule) => {
    const c = CASES[id];
    const fired = watchFindings(c.fires, { rules: enabled(rule) });
    const mine = fired.list.filter((f) => f.key === c.key);
    expect(mine).toHaveLength(1);
    expect(mine[0].text).toMatch(c.text);
    expect(watchFindings(c.silent, { rules: enabled(rule) }).list).toEqual([]);
  });

  it('N1 «progetto cambiato dopo la pubblicazione» e\' accesa: scatta col campo del server, tace senza', () => {
    const rule = WATCH_RULES.find((r) => r.id === 'changed_after_publish');
    expect(rule.enabled).toBe(true);
    expect(rule.field).toBe('source_changed_since_publish');
    expect(watchFindings(CASES.changed_after_publish.fires).list.map(f => f.rule.id)).toContain('changed_after_publish');
    expect(watchFindings(CASES.changed_after_publish.silent).list.filter(f => f.rule.id === 'changed_after_publish')).toEqual([]);
    // Non admin: il server manda null, la regola tace.
    expect(watchFindings(mini([P('p')], [X('x', 'p', { source_changed_since_publish: null })])).list
      .filter(f => f.rule.id === 'changed_after_publish')).toEqual([]);
  });

  it.each(WATCH_RULES.filter((r) => r.needs.length).map((r) => [r.id, r]))('%s: tace se manca una parte che le serve', (id, rule) => {
    const c = CASES[id];
    for (const part of rule.needs) {
      const graph = { ...c.fires, unavailable: new Set([part]) };
      expect(watchFindings(graph, { rules: enabled(rule) }).list).toEqual([]);
    }
  });

  it('ultima esecuzione non riuscita', () => {
    const graph = mini([P('a', 'estrai', { last_run: { status: 'failure', at: '2026-11-07T08:00:00Z' } })]);
    expect(watchFindings(graph).list.map((f) => f.text)).toEqual([expect.stringMatching(/^Ultima esecuzione non riuscita \(07\/11\)$/)]);
  });

  it('produzione: dati vecchi, Eseguibile senza input, nome doppio, nessuna pianificazione a monte', () => {
    const { list, byKey } = watchFindings(prod());
    expect(list.map((f) => `${f.rule.id} ${f.key}`).sort()).toEqual([
      'duplicate_name p:afe',
      'duplicate_name p:afm',
      'eseguibile_without_input x:ex-iapi',
      'no_schedule_upstream x:ex-iapi',
      // afe (20/07) rieseguito dopo l'ultima esecuzione di afc (16/07).
      'stale_sources p:afc',
      'stale_sources p:scm',
    ]);
    expect(byKey.get('p:scm')[0].text).toMatch(/^8 sorgenti sono state rieseguite dopo la sua ultima esecuzione \(10\/09\)/);
    expect(byKey.get('p:afm')[0].text).toBe('Stesso nome del progetto Estrai da cui legge');
    expect(byKey.get('p:afe')[0].text).toBe('Stesso nome del progetto Manipola che lo legge');
    expect(byKey.size).toBe(5);
  });
});

describe('evidenza a monte e a valle', () => {
  it('a monte (con la connessione) e a valle, transitivi', () => {
    const graph = prod();
    const lin = lineage(graph, 'p:iapi');
    expect([...lin.up].sort()).toEqual(['c:conn-1', 'p:ia']);
    expect([...lin.down]).toEqual(['x:ex-iapi']);
    const fromConn = lineage(graph, 'c:conn-1');
    expect(fromConn.up.size).toBe(0);
    expect(fromConn.down.has('x:ex-iapi')).toBe(true);
    expect(fromConn.down.has('p:cscm')).toBe(true);
    const fromSource = lineage(graph, `p:${SC8[0]}`);
    expect([...fromSource.down].sort()).toEqual(['p:cscm', 'p:scm']);
  });

  it('relazione di ogni scheda: selezionata, a monte, a valle, estranea; senza selezione nessuna', () => {
    const graph = prod();
    const lin = lineage(graph, 'p:iapi');
    expect(relationOf('p:iapi', 'p:iapi', lin)).toBe('sel');
    expect(relationOf('p:ia', 'p:iapi', lin)).toBe('up');
    expect(relationOf('x:ex-iapi', 'p:iapi', lin)).toBe('down');
    expect(relationOf('p:tk', 'p:iapi', lin)).toBe('none');
    expect(relationOf('p:tk', null, lineage(graph, null))).toBe(null);
  });
});

describe('filtri, ricerca, contatori, schede', () => {
  const graph = prod();
  const findings = watchFindings(graph);
  const chains = orderChains(buildChains(graph), findings);
  const matchesOf = (q) => (values) => values.flat().some((v) => String(v).toLowerCase().includes(q));

  it('Tutte, Da guardare, Pubblicati, Non usati', () => {
    expect(filterChains(chains, { filter: 'all', findings })).toHaveLength(5);
    expect(titles(filterChains(chains, { filter: 'warn', findings }))).toEqual(titles(chains).slice(0, 3));
    expect(titles(filterChains(chains, { filter: 'pub', findings }))).toEqual(['immobili attivi per istituto', 'SC_A_patrimonio_unita_AMM']);
    expect(titles(filterChains(chains, { filter: 'unused', findings }))).toEqual(['Non usati da altri progetti']);
  });

  it('ricerca per nome, tabella e connessione', () => {
    expect(titles(filterChains(chains, { findings, matches: matchesOf('ticket') }))).toEqual(['Non usati da altri progetti']);
    expect(titles(filterChains(chains, { findings, matches: matchesOf('afm_bl') }))).toEqual(['immobili attivi per istituto']);
    expect(filterChains(chains, { findings, matches: matchesOf('archibus') })).toHaveLength(5);
    expect(filterChains(chains, { findings, matches: matchesOf('niente') })).toEqual([]);
  });

  it('contatori: progetti, pubblicati, pianificazioni attive, connessioni usate', () => {
    expect(chainCounters(graph)).toEqual({ projects: 19, published: 2, schedulesActive: 0, connectionsUsed: 1 });
    expect(chainCounters({ ...graph, unavailable: new Set(['schedules', 'eseguibili']) })).toMatchObject({ published: null, schedulesActive: null });
  });

  it('riga di stato e segni delle schede', () => {
    expect(nodeMeta(graph.nodes.get('p:ia'))).toBe('Ultima esecuzione 07/11 · riuscita');
    expect(nodeMeta(graph.nodes.get('p:ia'), { runsUnavailable: true })).toBe('Stato non disponibile');
    expect(nodeMeta(graph.nodes.get('x:ex-iapi'))).toBe('Nessun input · versione 1');
    expect(nodeMeta(graph.nodes.get('x:ex-amm'))).toBe('1 input · versione 1');
    expect(nodeMeta(graph.nodes.get('c:conn-1'))).toBe('Database');
    expect(graph.nodes.get('c:conn-1').name).toBe('ARCHIBUS_EMULE');
    expect(nodeTags(graph.nodes.get('p:ia'))).toEqual(['Carica su database']);
    expect(nodeMeta(mini([P('n', 'estrai', { last_run: null })]).nodes.get('p:n'))).toBe('Mai eseguito');
  });
});

describe('«Esegui» dalla home (utils/pendingRun)', () => {
  it('vale una volta, solo per il progetto chiesto, per poco', () => {
    requestRunOnOpen('p1', 1000);
    expect(takeRunOnOpen('p2', 1000)).toBe(false);
    expect(takeRunOnOpen('p1', 1000)).toBe(true);
    expect(takeRunOnOpen('p1', 1000)).toBe(false);
    requestRunOnOpen('p1', 1000);
    expect(takeRunOnOpen('p1', 1000 + 60_001)).toBe(false);
  });
});

// Titoli delle catene modificabili (09/11/2026). Regola dichiarata in
// utils/projectChains (chainTitleOf): si salva sul progetto titolare (nodo
// del titolo, o progetto sorgente se e' un Eseguibile) e si legge dal
// progetto con `chain_title` piu' a valle (titolare, cammino, nome).
describe('titolo personalizzato della catena', () => {
  const chainOf = (projects, eseguibili = []) => buildChains(mini(projects, eseguibili)).find((c) => !c.isolated);

  it('rinomina: il titolo salvato sul nodo finale si mostra; calcolato come riserva', () => {
    const plain = chainOf([P('estr'), P('fine', 'manipola', { source_project_ids: ['estr'] })]);
    expect([plain.title, plain.customTitle, plain.titleOwnerKey]).toEqual(['fine', null, 'p:fine']);
    expect(chainTitleWrites(plain, '  Patrimonio  ')).toEqual([{ projectId: 'fine', chain_title: 'Patrimonio' }]);
    const named = chainOf([P('estr'), P('fine', 'manipola', { source_project_ids: ['estr'], chain_title: 'Patrimonio' })]);
    expect([named.title, named.computedTitle, named.customTitle]).toEqual(['Patrimonio', 'fine', 'Patrimonio']);
  });

  it('svuota: titolo calcolato, svuotato su tutti i progetti della catena che ne hanno uno', () => {
    const chain = chainOf([
      P('estr', 'estrai', { chain_title: 'Vecchio' }),
      P('fine', 'manipola', { source_project_ids: ['estr'], chain_title: 'Nuovo' }),
    ]);
    expect(chain.title).toBe('Nuovo');
    expect(chainTitleWrites(chain, '   ')).toEqual([
      { projectId: 'fine', chain_title: '' }, { projectId: 'estr', chain_title: '' },
    ]);
    const cleared = chainOf([P('estr', 'estrai', { chain_title: '' }), P('fine', 'manipola', { source_project_ids: ['estr'], chain_title: null })]);
    expect([cleared.title, cleared.customTitle]).toEqual(['fine', null]);
  });

  it('la catena cambia nodo finale: il titolo del vecchio finale non si perde', () => {
    // Prima: «fine» e' il finale e ha il titolo. Poi un progetto nuovo lo legge.
    const before = chainOf([P('estr'), P('fine', 'manipola', { source_project_ids: ['estr'], chain_title: 'Patrimonio' })]);
    const after = chainOf([
      P('estr'), P('fine', 'manipola', { source_project_ids: ['estr'], chain_title: 'Patrimonio' }),
      P('nuovo', 'manipola', { source_project_ids: ['fine'] }),
    ]);
    expect(before.title).toBe('Patrimonio');
    expect([after.titleKey, after.computedTitle, after.title]).toEqual(['p:nuovo', 'nuovo', 'Patrimonio']);
    // Rinominare ora salva sul nuovo finale (titolare), che ha la precedenza.
    expect(chainTitleWrites(after, 'Altro')).toEqual([{ projectId: 'nuovo', chain_title: 'Altro' }]);
  });

  it('nodo del titolo Eseguibile: titolare il suo progetto sorgente', () => {
    const chain = chainOf([P('estr'), P('man', 'manipola', { source_project_ids: ['estr'], chain_title: 'Pubblico' })], [X('zz-ex', 'man')]);
    expect([chain.titleKey, chain.titleOwnerKey, chain.title]).toEqual(['x:zz-ex', 'p:man', 'Pubblico']);
  });

  it('il titolo personalizzato si cerca; la modifica invalida il grafo della home', () => {
    const graph = mini([P('estr'), P('fine', 'manipola', { source_project_ids: ['estr'], chain_title: 'Patrimonio' })]);
    const chains = buildChains(graph);
    const matches = (...values) => values.flat().some((v) => String(v || '').toLowerCase().includes('patrim'));
    expect(filterChains(chains, { matches, findings: watchFindings(graph) }).map((c) => c.title)).toEqual(['Patrimonio']);
    expect(invalidationsForWrite('patch', '/projects/fine')).toContain('dashboardGraph');
  });
});
