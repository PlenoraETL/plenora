/**
 * Fase 5, T6 — stato locale dei nodi derivato dallo store (R1.9) e
 * aggiornamenti funzionali dei dati del nodo (R1.4, N5).
 *
 * Regole enumerate su TUTTI i componenti:
 *  1. nessun `useState(data.x)` / `useState(config.x)`: una copia locale dei
 *     dati del nodo non si riallinea dopo Ctrl+Z, Ctrl+Y o un ripristino.
 *     Le bozze di un campo passano da hooks/useStoreDraft.
 *  2. nessun `updateNodeData(id, { x: { ...data.x ... } })` ne'
 *     `{ config: { ...config } }`: dopo un await la copia del render e'
 *     vecchia; si usa `updateNodeData(id, d => ...)` o `patchNodeConfig`.
 *  3. nessun `useCanvasStore(s => s.nodes)` (tutti i nodi con le posizioni)
 *     nei componenti: un trascinamento li ridisegna a ogni frame (R9-U N6).
 * Eccezioni dichiarate: ProjectInputNode (tranche T5, form dei campi).
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { act, render, renderHook, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import { useStoreDraft } from '../../src/hooks/useStoreDraft';
import StaticValuesNode from '../../src/components/nodes/StaticValuesNode';
import { ToastProvider } from '../../src/context/ToastContext';

const SRC = path.resolve(import.meta.dirname, '../../src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? walk(p) : (/\.(jsx?|tsx?)$/.test(e.name) ? [p] : []);
});
const COMPONENTS = walk(path.join(SRC, 'components')).concat(walk(path.join(SRC, 'pages')));
const rel = (p) => path.relative(SRC, p).replace(/\\/g, '/');
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const T5_EXCEPTIONS = new Set(['components/nodes/ProjectInputNode.jsx']);

describe('regole enumerate su tutti i componenti', () => {
  it('nessuna copia locale dei dati del nodo (useState(data.x) / useState(config.x))', () => {
    const offenders = COMPONENTS.filter(p => !T5_EXCEPTIONS.has(rel(p)))
      .filter(p => /useState\(\s*(\(\)\s*=>\s*)?(data|config|node\.data|props\.data)\s*[.?[]/.test(code(fs.readFileSync(p, 'utf-8'))))
      .map(rel);
    expect(offenders).toEqual([]);
  });

  it('nessun aggiornamento dei dati che parte dalla copia del render', () => {
    const pattern = /(updateNodeData|patchExecParams|upd)\(\s*[\w.]+\s*,\s*\{[^}]*\.\.\.\s*\(?\s*(data|config|node\.data)\b/;
    const offenders = COMPONENTS.filter(p => !T5_EXCEPTIONS.has(rel(p)))
      .filter(p => pattern.test(code(fs.readFileSync(p, 'utf-8')))).map(rel);
    expect(offenders).toEqual([]);
  });

  it('nessun selettore di tutti i nodi con le posizioni nei componenti', () => {
    const pattern = /useCanvasStore\(\s*\(?\s*(s|state)\s*\)?\s*=>\s*(s|state)\.nodes\s*\)/;
    const offenders = COMPONENTS.filter(p => !T5_EXCEPTIONS.has(rel(p)))
      .filter(p => pattern.test(code(fs.readFileSync(p, 'utf-8')))).map(rel);
    expect(offenders).toEqual([]);
  });

  it('i nodi che avevano copie locali usano la bozza derivata dallo store', () => {
    for (const file of ['components/nodes/TableNode.jsx', 'components/nodes/FeatureLayerNode.jsx']) {
      expect(fs.readFileSync(path.join(SRC, file), 'utf-8')).toContain('useStoreDraft(');
    }
  });
});

describe('useStoreDraft: la bozza vive solo mentre si modifica', () => {
  it('lo store che cambia sotto la bozza (Ctrl+Z) la scarta: commit non riscrive il vecchio', () => {
    const writes = [];
    const { result, rerender } = renderHook(({ v }) => useStoreDraft(v), { initialProps: { v: 'a' } });
    act(() => result.current.begin());
    act(() => result.current.change('ab'));
    expect(result.current.editing).toBe(true);
    expect(result.current.value).toBe('ab');
    rerender({ v: 'zz' }); // undo/ripristino
    expect(result.current.editing).toBe(false);
    expect(result.current.value).toBe('zz');
    act(() => result.current.commit(v => writes.push(v)));
    expect(writes).toEqual([]);
    rerender({ v: 'a' }); // redo al valore di partenza: la bozza vecchia non riappare
    expect(result.current.editing).toBe(false);
  });

  it('commit scrive solo se il valore e\' cambiato', () => {
    const writes = [];
    const { result } = renderHook(() => useStoreDraft('x'));
    act(() => result.current.begin());
    act(() => result.current.commit(v => writes.push(v)));
    act(() => result.current.begin());
    act(() => result.current.change('y'));
    act(() => result.current.commit(v => writes.push(v)));
    expect(writes).toEqual(['y']);
  });
});

function LiveStatic({ id }) {
  const data = useCanvasStore(s => s.nodes.find(n => n.id === id)?.data);
  return data ? <StaticValuesNode id={id} data={data} /> : null;
}

describe('Valori statici: il pannello segue lo store (scenario R1.9)', () => {
  afterEach(() => { cleanup(); useCanvasStore.getState().reset(); });

  it('aggiungi un campo, Ctrl+Z: il campo sparisce dal pannello e la modifica dopo non lo riscrive', () => {
    useCanvasStore.getState().setReadOnly(false);
    useCanvasStore.getState().loadGraph([{ id: 's', type: 'staticValues', position: { x: 0, y: 0 },
      data: { fields: [{ id: 'f1', name: 'uno', value: '1', field_type: 'string' }] } }], []);
    render(<ToastProvider><LiveStatic id="s" /></ToastProvider>);
    act(() => useCanvasStore.getState().updateNodeData('s', d => ({
      fields: [...d.fields, { id: 'f2', name: 'due', value: '2', field_type: 'string' }] })));
    expect(screen.getAllByPlaceholderText('nome').map(i => i.value)).toEqual(['uno', 'due']);
    act(() => useCanvasStore.getState().undo());
    expect(screen.getAllByPlaceholderText('nome').map(i => i.value)).toEqual(['uno']);
    fireEvent.change(screen.getAllByPlaceholderText('nome')[0], { target: { value: 'primo' } });
    expect(useCanvasStore.getState().nodes[0].data.fields.map(f => f.name)).toEqual(['primo']);
  });
});

describe('aggiornamenti funzionali (R1.4, N5)', () => {
  afterEach(() => useCanvasStore.getState().reset());

  it('due upload in parallelo di extra_files: finiscono in ordine qualsiasi e restano entrambi', async () => {
    useCanvasStore.getState().setReadOnly(false);
    useCanvasStore.getState().loadGraph([{ id: 'a', type: 'api_enrich', position: { x: 0, y: 0 }, data: { extra_files: null } }], []);
    const onUploaded = (name, info) => useCanvasStore.getState().updateNodeData('a', d => ({ extra_files: { ...(d.extra_files || {}), [name]: info } }));
    let resolveA; let resolveB;
    const a = new Promise(r => { resolveA = r; }).then(() => onUploaded('A', { file_id: 'fa' }));
    const b = new Promise(r => { resolveB = r; }).then(() => onUploaded('B', { file_id: 'fb' }));
    resolveB(); await b; resolveA(); await a;
    expect(useCanvasStore.getState().nodes[0].data.extra_files).toEqual({ A: { file_id: 'fa' }, B: { file_id: 'fb' } });
  });

  it('ApiEnrichNode e MultiJoinNode aggiornano dai dati correnti dello store', () => {
    const api = fs.readFileSync(path.join(SRC, 'components/panels/ApiEnrichNode.jsx'), 'utf-8');
    expect(api).toMatch(/updateNodeData\(id, \(d\) => \(\{\s*extra_files: \{ \.\.\.\(d\.extra_files/);
    const mj = fs.readFileSync(path.join(SRC, 'components/transforms/MultiJoinNode.jsx'), 'utf-8');
    expect(mj).not.toMatch(/config: \{ \.\.\.config/);
    expect(mj).toContain('patchNodeConfig(id, patch)');
  });
});
