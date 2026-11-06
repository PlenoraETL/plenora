import { describe, it, expect, beforeEach } from 'vitest';

import useCanvasStore from '../../src/store/useCanvasStore';
import {
  CANVAS_COMMANDS, commandForKey, commandKeys, commandMenuItems, runCanvasCommand, useCanvasUi,
} from '../../src/components/editor/canvasCommands';
import { EDITOR_GUIDES } from '../../src/utils/guides/editorGuides';
import { resetSession } from '../../src/utils/session';
import { SHORTCUTS } from '../../src/utils/shortcuts';

/**
 * UI-3 (06/11/2026), U27: copia/incolla, «Duplica passo», ricerca nel
 * canvas, «Seleziona tutto» e collegamento da tastiera. Una tabella unica:
 * ogni comando ha tasti, nome, azione e voce di menu, e la guida degli
 * editor elenca proprio quei tasti.
 */
const node = (id, type = 'transform', extra = {}) => ({ id, type, position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: {} }, ...extra });

function load(nodes, edges = []) {
  const st = useCanvasStore.getState();
  st.reset();
  st.setReadOnly(false);
  st.loadGraph(nodes, edges);
}

beforeEach(() => { resetSession(); });

describe('one table of canvas commands', () => {
  it('every command has its shortcut in the single table, a name, an availability rule and an action', () => {
    const ids = CANVAS_COMMANDS.map(c => c.id);
    expect(ids).toEqual(['select_all', 'copy', 'paste', 'duplicate', 'search', 'connect', 'help']);
    for (const c of CANVAS_COMMANDS) {
      expect(commandKeys(c), c.id).toMatch(/\S/);
      expect(c.name).toMatch(/\S/);
      expect(typeof c.available).toBe('function');
      expect(typeof c.run).toBe('function');
    }
  });

  it('every key of the table resolves to its command, and only with the right modifier', () => {
    for (const c of CANVAS_COMMANDS) {
      const combo = commandKeys(c);
      const parts = combo.split('+');
      const key = parts[parts.length - 1];
      const ctrl = parts.includes('Ctrl');
      expect(commandForKey({ key: key.length === 1 ? key.toLowerCase() : key, ctrlKey: ctrl, metaKey: false, altKey: false, shiftKey: false }), combo).toBe(c.id);
      if (ctrl) expect(commandForKey({ key: key.toLowerCase(), ctrlKey: false, metaKey: false, altKey: false, shiftKey: false })).not.toBe(c.id);
    }
    expect(commandForKey({ key: 'z', ctrlKey: true })).toBeNull();
    // «a» senza Ctrl resta «Aggiungi un passo», non «Seleziona tutto».
    expect(commandForKey({ key: 'a', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false })).toBeNull();
  });

  it('the editor guides list the editor shortcuts of the single table, commands included', () => {
    const editor = SHORTCUTS.filter(s => s.group === 'Editor');
    for (const c of CANVAS_COMMANDS) expect(editor.some(s => s.id === (c.shortcut || c.id)), c.id).toBe(true);
    for (const guide of Object.values(EDITOR_GUIDES)) {
      const section = guide.sections.find(s => s.id === 'scorciatoie');
      const text = section.blocks.flatMap(b => b.ul || []).join('\n');
      for (const s of editor) expect(text, `${guide.slug}: ${s.id}`).toContain(s.label);
    }
  });

  it('menu items carry the reason when a command is not available; editing commands vanish in read-only', () => {
    load([]);
    const items = commandMenuItems(useCanvasStore.getState(), () => {});
    for (const i of items) if (i.disabled) expect(i.reason, i.key).toMatch(/\S/);
    useCanvasStore.getState().setReadOnly(true);
    const ro = commandMenuItems(useCanvasStore.getState(), () => {}).map(i => i.key);
    expect(ro).not.toContain('cmd-paste');
    expect(ro).not.toContain('cmd-duplicate');
    expect(ro).toContain('cmd-copy');
  });
});

describe('the commands act on the store through the user channels (one Ctrl+Z each)', () => {
  it('select all, copy and paste: a block with its inner edges, new ids, all selected', () => {
    load([node('a'), node('b')], [{ id: 'e1', source: 'a', target: 'b' }]);
    expect(runCanvasCommand('select_all').ok).toBe(true);
    expect(useCanvasStore.getState().nodes.every(n => n.selected)).toBe(true);
    expect(runCanvasCommand('copy').ok).toBe(true);
    const before = useCanvasStore.getState().past.length;
    expect(runCanvasCommand('paste').ok).toBe(true);
    const st = useCanvasStore.getState();
    expect(st.nodes).toHaveLength(4);
    const pasted = st.nodes.slice(2);
    expect(pasted.every(n => n.selected && !['a', 'b'].includes(n.id))).toBe(true);
    expect(st.edges).toHaveLength(2);
    expect(st.edges[1]).toMatchObject({ source: pasted[0].id, target: pasted[1].id });
    expect(st.past.length).toBe(before + 1);
  });

  it('duplicate keeps the same inputs; Output and loads are never copied', () => {
    load([node('src', 'project_source'), node('f'), node('out', 'result')], [
      { id: 'e1', source: 'src', target: 'f' }, { id: 'e2', source: 'f', target: 'out' },
    ]);
    useCanvasStore.getState().onNodesChange([{ id: 'f', type: 'select', selected: true }]);
    expect(runCanvasCommand('duplicate').ok).toBe(true);
    const st = useCanvasStore.getState();
    const copy = st.nodes.find(n => !['src', 'f', 'out'].includes(n.id));
    expect(st.edges.some(e => e.source === 'src' && e.target === copy.id)).toBe(true);
    // Solo l'Output selezionato: niente da duplicare, con il motivo.
    load([node('out', 'result')]);
    useCanvasStore.getState().onNodesChange([{ id: 'out', type: 'select', selected: true }]);
    const res = runCanvasCommand('duplicate');
    expect(res.ok).toBe(false);
    expect(res.message).toMatch(/Output/);
  });

  it('search, connect and help open their window; connect needs one selected node', () => {
    load([node('a'), node('b')]);
    expect(runCanvasCommand('connect').ok).toBe(false);
    useCanvasStore.getState().onNodesChange([{ id: 'a', type: 'select', selected: true }]);
    expect(runCanvasCommand('connect').ok).toBe(true);
    expect(useCanvasUi.getState().picker).toBe('connect');
    runCanvasCommand('search');
    expect(useCanvasUi.getState().picker).toBe('search');
    runCanvasCommand('help');
    expect(useCanvasUi.getState().help).toEqual({ section: 'transform' });
  });
});
