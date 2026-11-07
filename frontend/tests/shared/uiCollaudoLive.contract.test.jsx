/**
 * Collaudo dal vivo dopo il deploy di 42563405 (07/11/2026, docs/audits/
 * ui-collaudo-live-2026-11-07.md): una prova per voce, ognuna fallisce senza
 * la correzione.
 *
 *  L2 caricare il grafo alza tutti i contatori: la guida a canvas vuoto
 *     compare su un progetto nuovo; ogni selettore di useCanvasDerived legge
 *     solo i campi che fanno ricalcolare (enumerazione su src);
 *  L3 il canvas e la vista dicono che un ramo scollegato non viene eseguito;
 *  L4 id dei passi sostituiti dai titoli (Valida, esito fallito), esito
 *     riuscito dai dati strutturati con i numeri formattati, nessun nome di
 *     campo nella finestra «Prima di avviare»;
 *  L5 la conferma di eliminazione elenca i passi che resterebbero senza
 *     ingresso; il canvas li segna;
 *  L6 ricerca e «Collega a» con il riassunto del nodo come seconda riga,
 *     titolo del gruppo senza maiuscolo forzato;
 *  L7 all'uscita scelta si tolgono le copie di sicurezza di chi esce (non
 *     quelle delle sue schede aperte, non quelle degli altri utenti); con la
 *     sessione scaduta restano.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, act, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import { REVISION_FIELDS } from '../../src/store/graphRevisions';
import { GRAPH_FIELDS } from '../../src/hooks/useCanvasDerived';
import CanvasOverlays, { nodeItem } from '../../src/components/editor/CanvasOverlays';
import { analyzeCanvas } from '../../src/utils/canvasGraph';
import { withStepTitles } from '../../src/utils/stepTitles';
import { backendReportToResult } from '../../src/utils/backendValidationResult';
import { failureMessage, runSuccessText } from '../../src/utils/runFailure';
import { orphanedByDeletion, requestCanvasDeletion } from '../../src/utils/canvasDeletion';
import { nodeTitle } from '../../src/components/canvas/nodeDescriptors';
import { STORAGE_KEYS, storageKey } from '../../src/utils/browserStorage';
import { registeredSessionResets, resetSession } from '../../src/utils/session';
import { draftKey, touchTab, currentTabId } from '../../src/utils/localDrafts';
import { SRC, sourceFiles, stripComments } from '../helpers/sourceScan.js';

const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');

afterEach(() => {
  cleanup();
  useCanvasStore.getState().reset?.();
});

// ── L2 ──────────────────────────────────────────────────────────────────

describe('L2: caricare il grafo fa ricalcolare chi lo legge', () => {
  it('un progetto nuovo e vuoto: ogni contatore sale al caricamento e anche alla chiusura', () => {
    useCanvasStore.getState().reset();
    const before = useCanvasStore.getState();
    useCanvasStore.getState().loadGraph([], []);
    const after = useCanvasStore.getState();
    for (const field of Object.values(REVISION_FIELDS)) expect({ field, up: after[field] > before[field] }).toEqual({ field, up: true });
    useCanvasStore.getState().reset();
    const closed = useCanvasStore.getState();
    for (const field of Object.values(REVISION_FIELDS)) expect({ field, up: closed[field] > after[field] }).toEqual({ field, up: true });
  });

  it('la guida a canvas vuoto compare quando il progetto vuoto finisce di caricarsi', () => {
    useCanvasStore.getState().reset();
    useCanvasStore.setState({ projectId: 'p1', isReadOnly: false });
    render(<CanvasOverlays mode="manipola" canAdd onOpenPalette={() => {}} />);
    expect(screen.queryByText('Il canvas è vuoto')).toBeNull();
    act(() => { useCanvasStore.getState().loadGraph([], []); });
    expect(screen.getByText('Il canvas è vuoto')).toBeTruthy();
  });

  // Ogni chiamata di useCanvasDerived in src: il selettore (scritto li' o
  // definito con un nome) legge solo GRAPH_FIELDS dallo stato.
  const selectorText = (code, start) => {
    let depth = 0;
    let i = start;
    for (; i < code.length; i += 1) {
      const ch = code[i];
      if ('([{'.includes(ch)) depth += 1;
      else if (')]}'.includes(ch)) { if (depth === 0) break; depth -= 1; } else if ((ch === ',' || ch === ';') && depth === 0) break;
    }
    return code.slice(start, i);
  };
  const ALL = sourceFiles(/\.jsx?$/).map(f => ({ ...f, code: stripComments(f.code) }));
  const definitionOf = (name, file) => {
    const arrow = new RegExp(`(?:const|let)\\s+${name}\\s*=\\s*`);
    const fn = new RegExp(`function\\s+${name}\\s*\\(\\s*(\\w+)\\s*\\)\\s*`);
    for (const f of [file, ...ALL.filter(x => x !== file)]) {
      const a = f.code.match(arrow);
      if (a) return selectorText(f.code, a.index + a[0].length);
      const d = f.code.match(fn);
      if (d) return `(${d[1]}) => ${selectorText(f.code, d.index + d[0].length)}`;
    }
    return null;
  };
  const calls = ALL.flatMap(f => [...f.code.matchAll(/useCanvasDerived\(\s*/g)]
    .filter(m => !/function useCanvasDerived\($/.test(f.code.slice(Math.max(0, m.index - 30), m.index + 17)))
    .map(m => {
      let text = selectorText(f.code, m.index + m[0].length).trim();
      // Un nome (anche chiamato: `inspectedNode(nodeId)`) si legge dalla sua definizione.
      const named = text.match(/^(\w+)(?:\([^)]*\))?$/);
      if (named) text = definitionOf(named[1], f) || text;
      return { file: f.file, text };
    }));

  it('ogni selettore legge solo i campi che fanno ricalcolare (enumerazione)', () => {
    expect(calls.length).toBeGreaterThan(20);
    const wrong = [];
    for (const { file, text } of calls) {
      const chain = text.match(/^\s*(?:\(?\s*(\w+)\s*\)?\s*=>\s*\(?\s*)+/);
      if (!chain) { wrong.push(`${file}: selettore non riconosciuto: ${text.slice(0, 80)}`); continue; }
      const state = chain[1];
      for (const m of text.matchAll(new RegExp(`\\b${state}\\.(\\w+)`, 'g'))) {
        if (!GRAPH_FIELDS.includes(m[1])) wrong.push(`${file}: ${state}.${m[1]}`);
      }
    }
    expect(wrong).toEqual([]);
  });

  it('il controllo riconosce un campo che non fa ricalcolare (sonda)', () => {
    const empty = calls.find(c => c.file === 'components/editor/CanvasOverlays.jsx');
    expect(empty.text).toMatch(/s\.graphLoaded/);
    expect(GRAPH_FIELDS).toContain('graphLoaded');
    expect(GRAPH_FIELDS).not.toContain('isReadOnly');
  });
});

// ── L3 + L5: il canvas dice cosa non si esegue ────────────────────────────

const N = (id, type, data = {}) => ({ id, type, data, position: { x: 0, y: 0 } });
const E = (source, target, extra = {}) => ({ id: `${source}>${target}`, source, target, ...extra });

describe('L3/L5: rami scollegati e passi senza ingresso', () => {
  it('analyzeCanvas segna i passi senza ingresso (anche se arrivano all\'Output)', () => {
    const nodes = [N('src', 'file_source'), N('a', 'transform'), N('b', 'transform'), N('out', 'result')];
    const graph = analyzeCanvas(nodes, [E('a', 'b'), E('b', 'out')]);
    expect([...graph.inputless]).toEqual(['a']);
    expect(graph.dangling.has('a')).toBe(false);
  });

  it('il pannello e il nodo dicono che un ramo scollegato non viene eseguito', () => {
    expect(read('components/canvas/BranchSection.jsx')).toMatch(/non arriva all&apos;Output: non viene eseguito/);
    const compact = read('components/canvas/CompactNode.jsx');
    expect(compact).toMatch(/non viene eseguito/);
    expect(compact).toMatch(/inputless: \{ short: 'senza ingresso'/);
  });
});

// ── L4 ──────────────────────────────────────────────────────────────────

describe('L4: messaggi del backend nel linguaggio dell\'interfaccia', () => {
  const nodes = [N('transform_1730_ab', 'transform', { title: 'Solo attivi', transform_op: 'filter' }), N('transform_1730_abc', 'transform', { title: 'Join anagrafe' })];

  it('gli id dei passi diventano il loro titolo, anche tra apici e con id simili', () => {
    expect(withStepTitles("Nodo 'transform_1730_abc' richiede almeno un edge entrante; vedi transform_1730_ab.", nodes))
      .toBe(`Nodo «${nodeTitle('transform', nodes[1].data)}» richiede almeno un edge entrante; vedi «${nodeTitle('transform', nodes[0].data)}».`);
    expect(withStepTitles('nessun id qui', nodes)).toBe('nessun id qui');
  });

  it('Valida: il passo col titolo (nodeName e testo), il clic dal node_id', () => {
    const result = backendReportToResult({ issues: [{ node_id: 'transform_1730_ab', severity: 'error', code: 'structure.orphan', message: "Canvas contiene nodi orfani: transform_1730_ab" }] }, nodes);
    const [issue] = result.issues;
    expect(issue.nodeId).toBe('transform_1730_ab');
    expect(issue.nodeName).toBe(nodeTitle('transform', nodes[0].data));
    expect(issue.message).not.toMatch(/transform_1730/);
    expect(read('components/editor/PipelineRunner.jsx')).toMatch(/backendReportToResult\(report, st\.projectId === projectId \? st\.nodes : \[\]\)/);
  });

  it('esecuzione fallita: il messaggio coi titoli dei passi del canvas aperto', () => {
    expect(failureMessage({ success: false, message: 'Nodo transform_1730_ab (transform) ha 1 incoming edges' }, nodes)).not.toMatch(/transform_1730/);
    expect(read('components/modals/ExecutionModal.jsx')).toMatch(/failureMessage\(result, editorStepsFor\(projectId, useCanvasStore\.getState\(\)\)\)/);
  });

  it('esecuzione riuscita: righe e file dai dati strutturati, numero formattato', () => {
    const r = { success: true, message: 'Salvato 238044 righe in out.csv', rows_count: 238044, output_file: 'out.csv' };
    expect(runSuccessText(r)).toBe('238.044 righe salvate in «out.csv».');
    expect(runSuccessText({ success: true, rows_count: 1 })).toBe('1 riga salvata.');
    const modal = stripComments(read('components/modals/ExecutionModal.jsx'));
    expect(modal).toMatch(/: runSuccessText\(result\)/);
    expect(modal).not.toMatch(/result\.message \|\| 'Operazione eseguita con successo'/);
  });

  it('«Prima di avviare» non mostra nomi di campo', () => {
    expect(read('components/editor/BeforeRunModal.jsx')).not.toMatch(/>[^<]*intermediate_mode[^<]*</);
  });
});

// ── L5 ──────────────────────────────────────────────────────────────────

describe('L5: la conferma di eliminazione dice chi resta senza ingresso', () => {
  const nodes = [N('src', 'file_source', { file_name: 'clienti.csv' }), N('flt', 'transform', { title: 'Solo attivi' }),
    N('src2', 'file_source'), N('join', 'transform', { title: 'Unione' }), N('out', 'result')];
  const edges = [E('src', 'flt'), E('flt', 'join'), E('src2', 'join'), E('join', 'out'), E('inp', 'flt', { data: { edge_kind: 'parameter_binding' } })];

  it('orphanedByDeletion: solo chi perde TUTTI gli ingressi di dati', () => {
    const graph = { nodes, edges };
    expect(orphanedByDeletion(graph, { nodes: [nodes[0]] }).map(n => n.id)).toEqual(['flt']);
    expect(orphanedByDeletion(graph, { nodes: [nodes[2]] })).toEqual([]); // l'unione ha ancora flt
    expect(orphanedByDeletion(graph, { edges: [edges[3]] }).map(n => n.id)).toEqual(['out']);
  });

  it('ogni gesto (anche col testo del chiamante) mostra la frase', async () => {
    useCanvasStore.setState({ nodes, edges, isReadOnly: false });
    const seen = [];
    const confirm = async (cfg) => { seen.push(cfg.message); return false; };
    await requestCanvasDeletion({ confirm, nodes: [nodes[0]], perform: () => {} });
    await requestCanvasDeletion({ confirm, nodes: [nodes[0]], dialog: { title: 'Elimina', message: 'Eliminare la sorgente?' }, perform: () => {} });
    await requestCanvasDeletion({ confirm, edges: [edges[3]], perform: () => {} });
    expect(seen[0]).toMatch(/«Solo attivi» resterebbe senza ingresso/);
    expect(seen[1]).toMatch(/^Eliminare la sorgente\? Il passo «Solo attivi» resterebbe senza ingresso/);
    expect(seen[2]).toMatch(/resterebbe senza ingresso/);
  });
});

// ── L6 ──────────────────────────────────────────────────────────────────

describe('L6: ricerca e «Collega a» distinguono i nodi dello stesso tipo', () => {
  it('seconda riga = riassunto del nodo, come nel canvas', () => {
    const a = nodeItem(N('f1', 'transform', { transform_op: 'filter', config: { conditions: [{ column: 'stato', operator: '==', value: 'attivo' }] } }));
    const b = nodeItem(N('f2', 'transform', { transform_op: 'filter', config: { conditions: [{ column: 'comune', operator: '==', value: 'Roma' }] } }));
    expect(a.label).toBe(b.label);
    expect(a.desc).not.toBe(b.desc);
    expect(a.desc).toMatch(/stato/);
  });

  it('il titolo dei gruppi della palette non e\' in maiuscolo forzato', () => {
    const css = fs.readFileSync(path.join(SRC, 'components/canvas/NodePalette.css'), 'utf-8');
    const rule = css.match(/\.npal__group\s*\{[^}]*\}/)[0];
    expect(rule).not.toMatch(/text-transform:\s*uppercase/);
  });
});

// ── L7 ──────────────────────────────────────────────────────────────────

describe('L7: le copie di sicurezza all\'uscita dall\'account', () => {
  beforeEach(() => window.localStorage.clear());

  const seed = () => {
    const mine = draftKey('u1', 'p1', currentTabId());
    const closedTab = draftKey('u1', 'p2', 'scheda-chiusa');
    const openTab = draftKey('u1', 'p3', 'scheda-aperta');
    const other = draftKey('u2', 'p1', 'altra');
    touchTab('scheda-aperta');
    for (const k of [mine, closedTab, openTab, other]) window.localStorage.setItem(k, JSON.stringify({ nodes: [], savedAt: Date.now() }));
    return { mine, closedTab, openTab, other };
  };
  const present = (k) => window.localStorage.getItem(k) != null;

  it('uscita scelta: via quelle di chi esce, restano le sue schede aperte e gli altri utenti', () => {
    const k = seed();
    resetSession({ reason: 'logout', userId: 'u1' });
    expect([present(k.mine), present(k.closedTab), present(k.openTab), present(k.other)]).toEqual([false, false, true, true]);
  });

  it('sessione scaduta (o nessun motivo): restano tutte', () => {
    const k = seed();
    resetSession({ reason: 'expired', userId: 'u1' });
    resetSession();
    expect(Object.values(k).every(present)).toBe(true);
  });

  it('la regola sta nella tabella delle chiavi e la applica chi le conosce', () => {
    expect(STORAGE_KEYS.draft.logout).toBe('own');
    expect(storageKey('draft', 'u1', 'p', 't')).toBe(draftKey('u1', 'p', 't'));
    for (const row of Object.values(STORAGE_KEYS).filter(r => r.logout === 'own')) {
      expect(registeredSessionResets()).toContain(row.clearedBy);
    }
    const auth = stripComments(read('context/AuthContext.jsx'));
    expect(auth).toMatch(/resetSession\(\{ reason: expired \? 'expired' : 'logout', userId:/);
  });
});
