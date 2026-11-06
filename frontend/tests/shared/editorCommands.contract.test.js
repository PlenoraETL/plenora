/**
 * Conferma UI-1..UI-4 (06/11/2026), area «editor e funzioni nuove»: una
 * regola per classe, ciascuna enumerata.
 *
 *  - B-K2, B-K3: appunti per tipo di editor; i campi d'identita' dei nodi
 *    copiati (COPY_IDENTITY: chiave dell'Input runtime, alias della tabella
 *    di Estrai) restano univoci nel canvas;
 *  - B-K7: un tasto del canvas si prende solo se il comando parte;
 *  - B-K9: nome attuale delle sorgenti, risolto una volta, salvato al
 *    salvataggio;
 *  - B-K10: preferiti e recenti della palette per utente;
 *  - B-K11: destinazione dei caricamenti come il backend; alias automatico
 *    uguale al backend (tests/fixtures/selfJoinAliases.json);
 *  - B-K6: azioni dell'esecuzione fallita per permesso, passi solo dello
 *    stesso progetto, ticket col messaggio mostrato;
 *  - B-K8, C-K9: «Carica un file» solo se il file va nel gruppo del
 *    progetto e il progetto e' modificabile, esito vero;
 *  - C-K11: nessun tasto scritto a mano nei testi; «Recenti» per flag;
 *  - A-K6: la lista delle colonne di TableNode non muove il canvas.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, beforeEach } from 'vitest';

import useCanvasStore from '../../src/store/useCanvasStore';
import {
  CANVAS_COMMANDS, COPY_IDENTITY, canvasKeyDecision, runCanvasCommand,
} from '../../src/components/editor/canvasCommands';
import {
  NODES, availability, isSingleUseNode, isUnresolvedSource, loadDestination, withResolvedSourceNames,
} from '../../src/components/editor/editorNodeCatalog';
import { editorUploadAllowed, editorUploadMessage } from '../../src/components/editor/editorUpload';
import { paletteMemoryKey, readPaletteMemory, rememberPick } from '../../src/components/canvas/paletteMemory';
import { serializeCanvas } from '../../src/utils/canvasSerializer';
import { selfJoinAlias } from '../../src/utils/estraiJoins';
import { editorStepsFor, failureActions, failureMessage, failureTicketDraft } from '../../src/utils/runFailure';
import { humanizeError } from '../../src/utils/errorMessages';
import { SHORTCUTS, shortcutText } from '../../src/utils/shortcuts';
import { TERMS } from '../../src/utils/glossary';
import { resetSession } from '../../src/utils/session';
import { SRC, sourceFiles, stripComments } from '../helpers/sourceScan.js';

const ALIAS_CASES = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, '../fixtures/selfJoinAliases.json'), 'utf-8')).cases;

function load(nodes, edges = [], { mode = 'manipola', readOnly = false } = {}) {
  const st = useCanvasStore.getState();
  st.reset();
  st.init('p1', mode);
  st.setReadOnly(false);
  st.loadGraph(nodes, edges);
  if (readOnly) st.setReadOnly(true);
}
const select = (ids) => useCanvasStore.setState(s => ({ nodes: s.nodes.map(n => ({ ...n, selected: ids.includes(n.id) })) }));
const nodesOf = (type) => useCanvasStore.getState().nodes.filter(n => n.type === type);

const input = (id, key, order = 0) => ({ id, type: 'project_input', position: { x: 0, y: 0 },
  data: { input_key: key, label: key, input_kind: 'text', order, bindings: [] } });
const table = (id, tableName, alias = null) => ({ id, type: 'tableNode', position: { x: 0, y: 0 },
  data: { table_name: tableName, alias, columns: [], filters: [] } });

beforeEach(() => { resetSession(); try { window.localStorage.clear(); window.sessionStorage.clear(); } catch { /* niente */ } });

describe('B-K2: clipboard per editor type', () => {
  it('nodes copied in one editor type paste only in an editor of the same type, with the reason', () => {
    load([{ id: 't1', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: {} } }], [], { mode: 'manipola' });
    select(['t1']);
    expect(runCanvasCommand('copy').ok).toBe(true);
    load([], [], { mode: 'estrai' });
    const res = runCanvasCommand('paste');
    expect(res.ok).toBe(false);
    expect(res.message).toMatch(/Manipola.*stesso tipo/);
    expect(useCanvasStore.getState().nodes).toEqual([]);
    load([], [], { mode: 'manipola' });
    expect(runCanvasCommand('paste').ok).toBe(true);
    expect(nodesOf('transform')).toHaveLength(1);
  });
});

describe('B-K2, B-K3: identity fields of copied nodes stay unique (COPY_IDENTITY)', () => {
  // Ogni tipo con un campo d'identita': il nodo, il campo e il valore effettivo.
  const IDENTITY = {
    project_input: { mode: 'manipola', make: input, field: (n) => n.data.input_key, args: ['input_1'] },
    tableNode: { mode: 'estrai', make: table, field: (n) => (n.data.alias || n.data.table_name).toLowerCase(), args: ['ordini'] },
  };

  it('every identity rule is enumerated here', () => {
    expect(Object.keys(COPY_IDENTITY).sort()).toEqual(Object.keys(IDENTITY).sort());
  });

  it.each(Object.entries(IDENTITY))('%s: copy+paste and duplicate never repeat the identity field', (type, spec) => {
    for (const cmd of ['paste', 'duplicate']) {
      load([spec.make('a', ...spec.args)], [], { mode: spec.mode });
      select(['a']);
      if (cmd === 'paste') { runCanvasCommand('copy'); runCanvasCommand('paste'); runCanvasCommand('paste'); } else {
        runCanvasCommand('duplicate'); select(['a']); runCanvasCommand('duplicate');
      }
      const values = nodesOf(type).map(spec.field);
      expect(values, `${type}/${cmd}`).toHaveLength(3);
      expect(new Set(values).size, `${type}/${cmd}: ${values}`).toBe(3);
    }
  });

  it('a copied runtime input goes after the highest order; a copied table gets the backend alias form', () => {
    load([input('a', 'input_1', 4)], [], { mode: 'manipola' });
    select(['a']); runCanvasCommand('duplicate');
    const copy = nodesOf('project_input').find(n => n.id !== 'a');
    expect(copy.data).toMatchObject({ input_key: 'input_1_2', order: 5 });
    load([table('a', 'dbo.ordini', 'o')], [], { mode: 'estrai' });
    select(['a']); runCanvasCommand('duplicate');
    expect(nodesOf('tableNode').find(n => n.id !== 'a').data.alias).toBe('ordini_2');
  });
});

describe('B-K7: a canvas key is taken only when its command runs', () => {
  const states = {
    empty: { nodes: [], isReadOnly: false, mode: 'manipola' },
    oneSelected: { nodes: [{ id: 'x', type: 'transform', selected: true, data: {} }], edges: [], isReadOnly: false, mode: 'manipola' },
  };
  it.each(CANVAS_COMMANDS.map(c => c.id))('%s: unavailable or read-only -> left to the browser', (id) => {
    const cmd = CANVAS_COMMANDS.find(c => c.id === id);
    for (const [name, st] of Object.entries(states)) {
      for (const readOnly of [false, true]) {
        for (const onFlow of [false, true]) {
          const state = { ...st, isReadOnly: readOnly };
          const decision = canvasKeyDecision(id, state, { onFlow });
          const reason = cmd.available(state);
          const runs = !(cmd.edits && readOnly) && !reason;
          expect(decision.take, `${id}/${name}/ro=${readOnly}/flow=${onFlow}`).toBe(runs);
          // Avviso solo dentro il canvas, mai per un comando che modifica in sola lettura.
          const warns = !runs && onFlow && !(cmd.edits && readOnly) ? reason : null;
          expect(decision.warn, `${id}/${name}/ro=${readOnly}/flow=${onFlow}`).toBe(warns);
        }
      }
    }
  });

  it('the keyboard handler asks the decision before preventDefault', () => {
    const code = stripComments(fs.readFileSync(path.join(SRC, 'components/editor/CanvasOverlays.jsx'), 'utf-8'));
    const decision = code.indexOf('canvasKeyDecision(');
    expect(decision).toBeGreaterThan(-1);
    expect(code.indexOf('e.preventDefault()')).toBeGreaterThan(decision);
  });
});

describe('B-K9: source names follow the catalog', () => {
  const src = (id, extra = {}) => ({ id, type: 'project_source', position: { x: 0, y: 0 }, data: { source_project_id: id, project_name: 'vecchio', columns: [], ...extra } });

  it('found -> current name; not in the list -> resolved as null and not retried; new list -> re-resolved', () => {
    const nodes = [src('p1'), src('p9')];
    const once = withResolvedSourceNames(nodes, { projects: [{ id: 'p1', name: 'Nuovo' }] });
    expect(once.map(n => n.data._sourceName)).toEqual(['Nuovo', null]);
    expect(once.some(isUnresolvedSource)).toBe(false);
    expect(withResolvedSourceNames(once, { projects: [{ id: 'p1', name: 'Nuovo' }] })).toBe(once);
    const renamed = withResolvedSourceNames(once, { projects: [{ id: 'p1', name: 'Rinominato' }, { id: 'p9', name: 'Trovato' }] });
    expect(renamed.map(n => n.data._sourceName)).toEqual(['Rinominato', 'Trovato']);
  });

  it('saving writes the current name; an unknown name keeps the saved copy', () => {
    const nodes = withResolvedSourceNames([src('p1'), src('p9')], { projects: [{ id: 'p1', name: 'Nuovo' }] });
    const saved = serializeCanvas(nodes, []).nodes;
    expect(saved.map(n => n.project_name)).toEqual(['Nuovo', 'vecchio']);
    expect(saved.every(n => !('_sourceName' in n))).toBe(true);
  });
});

describe('B-K10: palette memory belongs to the signed-in user', () => {
  const pick = { id: 'filter', kind: 'transform' };
  it('two users on the same browser do not see each other\'s recent picks; no user, no memory', () => {
    window.sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'anna' }));
    rememberPick('manipola', pick);
    expect(readPaletteMemory('manipola').recent).toEqual(['transform:filter']);
    window.sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'bruno' }));
    expect(readPaletteMemory('manipola').recent).toEqual([]);
    window.sessionStorage.removeItem('urby_user');
    expect(paletteMemoryKey('manipola')).toBeNull();
    rememberPick('manipola', pick);
    // Conferma finale G8: chiave della tabella unica (prefisso `plenora:`).
    expect(Object.keys(window.localStorage).filter(k => k.startsWith('plenora:palette:'))).toEqual(['plenora:palette:anna:manipola']);
  });

  it('the old per-browser key is removed on first read (owner unknown: given to nobody)', () => {
    window.localStorage.setItem('plenora.palette.manipola', JSON.stringify({ recent: ['transform:x'] }));
    window.sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'anna' }));
    expect(readPaletteMemory('manipola').recent).toEqual([]);
    expect(window.localStorage.getItem('plenora.palette.manipola')).toBeNull();
  });

  it('conferma finale B-K10: the old per-user key moves to the new key once', () => {
    window.localStorage.setItem('plenora.palette.user:anna.estrai', JSON.stringify({ recent: ['table:t1'] }));
    window.sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'anna' }));
    expect(readPaletteMemory('estrai').recent).toEqual(['table:t1']);
    expect(window.localStorage.getItem('plenora.palette.user:anna.estrai')).toBeNull();
    expect(window.localStorage.getItem('plenora:palette:anna:estrai')).not.toBeNull();
  });
});

describe('B-K11: load destination is the backend write branch', () => {
  const carica = (dest) => ({ id: `c_${dest}`, type: 'carica', data: dest ? { dest_type: dest } : {} });
  it('a «Carica su database» node writing to ArcGIS blocks «Carica su ArcGIS», not the DB load', () => {
    const output = { id: 'r', type: 'result', data: {} };
    expect(['db', 'arcgis', 'rest', undefined].map(d => loadDestination(carica(d)))).toEqual(['db', 'arcgis', 'rest', 'db']);
    expect(loadDestination({ type: 'carica_arcgis' })).toBe('arcgis');
    expect(loadDestination({ type: 'transform' })).toBeNull();
    expect(availability('load_arcgis', [output, carica('arcgis')]).ok).toBe(false);
    expect(availability('load_db', [output, carica('arcgis')]).ok).toBe(true);
    expect(availability('load_db', [output, carica('db')]).ok).toBe(false);
    // Ogni caricamento (anche REST) resta un nodo unico, che non si copia.
    for (const d of ['db', 'arcgis', 'rest']) expect(isSingleUseNode(carica(d))).toBe(true);
    expect(NODES.load_db.present(carica('rest'))).toBe(false);
  });

  it.each(ALIAS_CASES)('automatic alias of $table = backend ($expected)', ({ table: name, taken, expected }) => {
    expect(selfJoinAlias(name, taken)).toBe(expected);
  });
});

describe('B-K6: actions of a failed run', () => {
  const roles = ['esecutore', 'viewer', 'user', 'admin', 'superadmin'];
  it.each(roles)('%s: log and report only where the backend allows them', (role) => {
    const actions = failureActions({ role, user_id: 'u' }, { step: null });
    const normal = role !== 'esecutore';
    expect(actions).toEqual({ goToStep: false, openLog: normal, report: normal });
    expect(failureActions(null)).toEqual({ goToStep: false, openLog: false, report: false });
  });

  it('steps only from the editor of the same project; the ticket carries the shown message', () => {
    const st = { projectId: 'p1', nodes: [{ id: 'n' }] };
    expect(editorStepsFor('p1', st)).toBe(st.nodes);
    expect(editorStepsFor('p2', st)).toEqual([]);
    expect(editorStepsFor(null, st)).toEqual([]);
    const result = { success: false, code: 'permission_revoked', message: 'Traceback (most recent call last): segreto' };
    expect(failureMessage(result)).toBe(humanizeError(result).message);
    const draft = failureTicketDraft({ projectName: 'X', result });
    expect(draft.description).toContain(`Messaggio: ${failureMessage(result)}`);
  });
});

describe('B-K8, C-K9: «Carica un file» from the editor', () => {
  const user = { role: 'user', user_id: 'u', group_id: 'g1' };
  const project = (group) => ({ metadata: { group_id: group } });
  it('offered only to who can upload, on an editable project of the user\'s group', () => {
    expect(editorUploadAllowed({ user, project: project('g1'), isReadOnly: false })).toBe(true);
    expect(editorUploadAllowed({ user, project: project('g1'), isReadOnly: true })).toBe(false);
    expect(editorUploadAllowed({ user: { ...user, role: 'admin' }, project: project('g2'), isReadOnly: false })).toBe(false);
    expect(editorUploadAllowed({ user: { ...user, role: 'viewer' }, project: project('g1'), isReadOnly: false })).toBe(false);
    expect(editorUploadAllowed({ user, project: null, isReadOnly: false })).toBe(false);
  });

  it('the outcome says «added to the canvas» only when the node was added', () => {
    expect(editorUploadMessage({ name: 'a.csv' }, true)).toMatch(/aggiunto al canvas/);
    expect(editorUploadMessage({ name: 'a.csv' }, false)).not.toMatch(/canvas/);
  });

  it('both editors use the single rule (no own permission check, no own message)', () => {
    for (const page of ['pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx']) {
      const code = stripComments(fs.readFileSync(path.join(SRC, page), 'utf-8'));
      expect(code, page).toContain('useEditorFileUpload(');
      expect(code, page).not.toMatch(/can\.uploadFile|aggiunto al canvas/);
    }
  });
});

describe('C-K11: keys in texts come from the shortcuts table', () => {
  it('no visible text writes a key combination by hand', () => {
    const HAND_KEYS = /Ctrl\+[A-Z0-9]|⌘|\(\/ o A\)|Cmd\+/;
    const offenders = [];
    for (const { file, code } of sourceFiles(/\.jsx?$/)) {
      if (file === 'utils/shortcuts.js') continue;
      stripComments(code).split('\n').forEach((line, i) => { if (HAND_KEYS.test(line)) offenders.push(`${file}:${i + 1}: ${line.trim()}`); });
    }
    expect(offenders).toEqual([]);
  });

  it('glossary descriptions show the keys of the table', () => {
    expect(TERMS.save.description).toContain(shortcutText('save'));
    expect(TERMS.add_step.description).toContain(shortcutText('add_step'));
    for (const s of SHORTCUTS) expect(shortcutText(s), s.id).toMatch(/\S/);
  });

  it('the palette recognizes «Recenti» by flag, not by its text', () => {
    const code = stripComments(fs.readFileSync(path.join(SRC, 'components/canvas/NodePalette.jsx'), 'utf-8'));
    expect(code).not.toMatch(/label\s*===\s*'Recenti'/);
  });
});

describe('A-K6 / conferma finale F1: the column list of TableNode', () => {
  it('leaves nowheel/nodrag to VirtualList (only when the box really scrolls)', () => {
    const code = stripComments(fs.readFileSync(path.join(SRC, 'components/nodes/TableNode.jsx'), 'utf-8'));
    expect(code).toMatch(/<VirtualList[\s\S]*?\binCanvasNode\b/);
    // Nessun contenitore proprio con le classi: sotto la soglia la rotella zooma.
    expect(code).not.toMatch(/className="[^"]*\b(nowheel|nodrag)\b/);
  });
});
