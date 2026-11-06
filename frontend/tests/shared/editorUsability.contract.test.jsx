import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { describe, it, expect, afterEach, vi } from 'vitest';

import { EDITOR_GUIDES, EMPTY_CANVAS_HINTS, guideSectionFor } from '../../src/utils/guides/editorGuides';
import { guideToMarkdown } from '../helpers/guideMarkdown';
import { failedStepOf, failureTicketDraft, runLogPath } from '../../src/utils/runFailure';
import NodePalette, { filterPalette } from '../../src/components/canvas/NodePalette';
import { selfJoinAlias } from '../../src/utils/estraiJoins';

/**
 * UI-3 (06/11/2026): usabilita' e funzioni degli editor, una regola per
 * classe di difetti, con enumerazione:
 *  - P24: nessun componente definito dentro il render di un altro;
 *  - U30: una guida per ogni editor, la stessa nell'app e nel repository;
 *  - U16: passo fallito, registro e segnalazione da un solo modulo;
 *  - U19, U29: palette senza elenchi tagliati in silenzio, preferiti e recenti;
 *  - P29: alias della tabella aggiunta di nuovo.
 */
afterEach(() => { cleanup(); try { window.localStorage.clear(); } catch { /* niente */ } });

const REPO = path.resolve(import.meta.dirname, '../../..');
const SRC = path.join(REPO, 'frontend', 'src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? walk(p) : (/\.jsx$/.test(e.name) ? [p] : []);
});

describe('P24: no component defined inside another component', () => {
  it('no indented `const Name = () =>` or `function Name(` in any .jsx', () => {
    const NESTED = /^[ \t]{2,}(const [A-Z][A-Za-z0-9]* = (\([^)]*\)|[a-z]+) =>|function [A-Z][A-Za-z0-9]*\()/m;
    const offenders = walk(SRC).filter(f => NESTED.test(fs.readFileSync(f, 'utf-8'))).map(f => path.relative(SRC, f));
    expect(offenders).toEqual([]);
    const ds = fs.readFileSync(path.join(SRC, 'components/nodes/DataSourceNode.jsx'), 'utf-8');
    expect(ds).toMatch(/^function BreadCrumb\(/m);
    expect(ds).toMatch(/^function DsSelector\(/m);
  });
});

describe('U30: one guide per editor, in the app and in the repository', () => {
  it.each(Object.entries(EDITOR_GUIDES))('%s: docs/guide is the in-app guide', (mode, guide) => {
    const doc = path.join(REPO, 'docs', 'guide', `${guide.slug}.md`);
    const expected = guideToMarkdown(guide, 'frontend/src/utils/guides/editorGuides.js', "dal menu «⋯» dell'editor, dal canvas vuoto e col tasto F1");
    if (process.env.UPDATE_GUIDES) fs.writeFileSync(doc, expected, 'utf-8');
    expect(fs.readFileSync(doc, 'utf-8').replace(/\r\n/g, '\n')).toBe(expected);
    expect(fs.readFileSync(path.join(REPO, 'docs', 'README.md'), 'utf-8')).toContain(`guide/${guide.slug}.md`);
    expect(EMPTY_CANVAS_HINTS[mode]).toMatch(/\S/);
  });

  it('contextual help: every node type an editor adds has its section', () => {
    const types = {
      estrai: ['tableNode', 'resultNode', 'carica'],
      manipola: ['project_source', 'file_source', 'transform', 'multi_join', 'result', 'carica'],
      arricchisci: ['project_source', 'api_enrich', 'result'],
    };
    for (const [mode, list] of Object.entries(types)) {
      for (const t of list) expect(guideSectionFor(EDITOR_GUIDES[mode], t), `${mode}:${t}`).toBeTruthy();
    }
    expect(guideSectionFor(EDITOR_GUIDES.manipola, null)).toBeNull();
  });
});

describe('U16: a failed run says which step failed and offers log and report', () => {
  const nodes = [
    { id: 'transform_1', type: 'transform', data: { transform_op: 'filter', config: {} } },
    { id: 'api_enrich_9', type: 'api_enrich', data: { api_name: 'Geocoder' } },
  ];

  it('the backend node id wins; then an id in the message; never a guess', () => {
    expect(failedStepOf({ success: false, error_node_id: 'api_enrich_9', message: 'x' }, nodes)?.id).toBe('api_enrich_9');
    expect(failedStepOf({ success: false, message: 'Errore nel nodo transform_1: colonna' }, nodes)?.id).toBe('transform_1');
    expect(failedStepOf({ success: false, message: 'Errore generico' }, nodes)).toBeNull();
    expect(failedStepOf({ success: true }, nodes)).toBeNull();
  });

  it('log path and ticket draft carry project, run, step and message', () => {
    expect(runLogPath({ projectId: 'p1' })).toBe('/logs?tab=runs&project=p1&status=failure');
    const draft = failureTicketDraft({ projectName: 'Scheda', projectId: 'p1', projectType: 'manipola',
      result: { task_id: 't9', message: 'boom', code: 'x_y' }, step: { id: 'transform_1', title: 'Filtra' } });
    expect(draft.category).toBe('pipeline');
    for (const part of ['Scheda', 'p1', 't9', 'boom', 'Filtra', 'transform_1', 'x_y']) expect(draft.description).toContain(part);
  });
});

describe('U19, U29: the palette never cuts silently and remembers favorites and recent picks', () => {
  const groups = [
    { label: 'Più usate', onlyWhenEmpty: true, fallback: true, items: [{ id: 'filter', kind: 'transform', label: 'Filtra' }] },
    { label: 'Tabelle', limit: 2, items: [
      { id: 't1', kind: 'table', label: 'clienti', keywords: ['codice_fiscale'] },
      { id: 't2', kind: 'table', label: 'ordini' },
      { id: 't3', kind: 'table', label: 'righe' },
    ] },
  ];

  it('a group over its limit says how many are hidden; columns are searched', () => {
    const shown = filterPalette(groups, '');
    expect(shown.find(g => g.label === 'Tabelle')).toMatchObject({ hidden: 1 });
    expect(filterPalette(groups, 'fiscale')[0].items.map(i => i.id)).toEqual(['t1']);
    render(<NodePalette open groups={groups} onPick={() => {}} onClose={() => {}} />);
    expect(screen.getByText(/Altre 1 voci non mostrate/)).toBeTruthy();
  });

  it('a pick becomes «Recenti» (replacing «Più usate»), a star makes it a favorite', () => {
    const onPick = vi.fn();
    const { unmount } = render(<NodePalette open scope="test" groups={groups} onPick={onPick} onClose={() => {}} />);
    fireEvent.click(screen.getByText('ordini'));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 't2' }));
    unmount();
    render(<NodePalette open scope="test" groups={groups} onPick={() => {}} onClose={() => {}} />);
    expect(screen.getByText('Recenti')).toBeTruthy();
    expect(screen.queryByText('Più usate')).toBeNull();
    act(() => { fireEvent.click(screen.getAllByRole('button', { name: /Aggiungi clienti ai preferiti/ })[0]); });
    expect(screen.getByText('Preferiti')).toBeTruthy();
  });
});

describe('P29: the same table added again gets its own alias', () => {
  it('first free alias <table>_N, identifier only', () => {
    expect(selfJoinAlias('ordini', ['ordini'])).toBe('ordini_2');
    expect(selfJoinAlias('ordini', ['ordini', 'ordini_2'])).toBe('ordini_3');
    expect(selfJoinAlias('dbo.Ordini Righe', ['dbo.Ordini Righe'])).toBe('Ordini_Righe_2');
    expect(selfJoinAlias('2024', ['2024'])).toBe('t_2024_2');
    expect(selfJoinAlias('EMP', ['EMP', 'emp_2'])).toBe('EMP_3');
  });
});
