import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Server finto con la regola del backend (canvas_revision.py): il token
// cambia solo quando cambia il canvas; una base diversa dal token corrente
// e' rifiutata con 409 canvas_version_conflict, senza toccare il canvas.
// `versioned`: il server restituisce il token nel GET del progetto (regola
// della risposta senza token, utils/canvasVersion.tokenAfterWrite).
const { server, gate } = vi.hoisted(() => ({
  server: { version: 'c1:v1', canvas: null, n: 1, puts: [], restores: [], gets: 0, versioned: true, updatedAt: '2026-11-04T10:00:00Z' },
  // Attesa delle colonne derivate prima del PUT (punto 3): null = nessuna attesa.
  gate: { promise: null },
}));
const conflict = () => Object.assign(new Error('Il canvas sul server è più recente della versione di base'), {
  response: { status: 409, data: { detail: { error: 'validation_error', reason: 'canvas_version_conflict', message: 'Il canvas sul server è più recente della versione di base', canvas_version: server.version, updated_at: '2026-11-04T10:30:00Z' } } },
});
const write = (canvas) => {
  server.n += 1;
  server.canvas = canvas;
  server.version = `c1:v${server.n}`;
  server.updatedAt = `2026-11-04T10:${String(server.n).padStart(2, '0')}:00Z`;
  return { status: 'ok', canvas_version: server.version, updated_at: server.updatedAt };
};
const withoutToken = (response) => {
  const out = { ...response };
  delete out.canvas_version;
  return out;
};
const PUT = async (_pid, body) => {
  server.puts.push(body);
  if ('base_canvas_version' in body && body.base_canvas_version !== server.version) throw conflict();
  // eslint-disable-next-line no-unused-vars
  const { base_canvas_version, ...canvas } = body;
  return write(canvas);
};
vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn((...args) => PUT(...args)),
  restoreCanvasVersion: vi.fn(async (_pid, versionId, base = null) => {
    server.restores.push({ versionId, base });
    if (base && base !== server.version) throw conflict();
    return { canvas: { nodes: [], edges: [] }, ...write({ restored: versionId }) };
  }),
  getProject: vi.fn(async (pid) => {
    server.gets += 1;
    return { metadata: { id: pid, updated_at: server.updatedAt }, canvas_version: server.versioned ? server.version : null };
  }),
  // Valida non cambia il canvas (regola del backend): il token resta.
  validateProjectCanvas: vi.fn(async () => ({ valid: true, has_errors: false, issues: [] })),
}));
vi.mock('../../src/store/useColumnStore', async (orig) => {
  const real = await orig();
  return { ...real, derivedColumnsForSave: vi.fn((...args) => gate.promise || real.derivedColumnsForSave(...args)) };
});

import useCanvasStore, { createCanvasStore } from '../../src/store/useCanvasStore';
import { updateCanvas, restoreCanvasVersion, validateProjectCanvas } from '../../src/api/projects';
import PipelineRunner from '../../src/components/editor/PipelineRunner';
import { overwriteServer, pageReload, reloadServerVersion, restoreVersion, saveBeforeServerRead } from '../../src/utils/serverActions';
import { serverVersionOf } from '../../src/utils/canvasVersion';
import { draftKey, readDraft } from '../../src/utils/localDrafts';
import SaveConflictNotice from '../../src/components/editor/SaveConflictNotice';

/**
 * Contract Blocco A, regola 8 (F3, raccolta h.4): salvataggio condizionato
 * alla versione. Tabella delle transizioni in utils/canvasVersion.js; ogni
 * riga ha qui il suo test (CANVAS_VERSION_TRANSITIONS).
 */
const SRC = path.resolve(__dirname, '../../src');
const files = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(d => {
  const p = path.join(dir, d.name);
  return d.isDirectory() ? files(p) : (/\.(jsx?|tsx?)$/.test(d.name) ? [p] : []);
});
const code = (p) => fs.readFileSync(p, 'utf-8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const rel = (p) => path.relative(SRC, p).split(path.sep).join('/');
const usersOf = (re) => files(SRC).filter(p => re.test(code(p))).map(rel).sort();

const n = (id, type = 'transform', data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
// Il canvas salvato e' piatto (utils/canvasSerializer): i campi del nodo in cima.
const serverLabel = () => server.canvas?.nodes?.find(x => x.id === 's')?.label;
const PROJECT = () => ({ metadata: { id: 'p1', updated_at: '2026-11-04T10:00:00Z' }, canvas_version: server.version });

/** Una scheda: uno store che apre il progetto come l'editor (GET -> loadGraph). */
function openTab(store = createCanvasStore(), project = PROJECT()) {
  const st = store.getState();
  st.setReadOnly(false);
  st.init('p1', 'manipola');
  st.loadGraph([n('s', 'project_source'), n('o', 'result')], [], serverVersionOf(project));
  return {
    store,
    edit: (label) => store.getState().updateNodeData('s', { label }),
    save: () => store.getState().handleSaveQuiet(),
  };
}

beforeEach(() => {
  Object.assign(server, { version: 'c1:v1', canvas: null, n: 1, puts: [], restores: [], gets: 0, versioned: true, updatedAt: '2026-11-04T10:00:00Z' });
  gate.promise = null;
  validateProjectCanvas.mockClear();
  updateCanvas.mockClear();
  restoreCanvasVersion.mockClear();
  localStorage.clear();
  sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 7, username: 'mario', role: 'user' }));
  useCanvasStore.getState().reset();
  pageReload.run = vi.fn();
});

const CANVAS_VERSION_TRANSITIONS = {
  'apertura': async () => {
    const A = openTab();
    expect(A.store.getState().canvasVersion).toBe('c1:v1');
    expect(A.store.getState().lastSavedServerAt).toBe('2026-11-04T10:00:00Z');
  },

  'salvataggio riuscito': async () => {
    const A = openTab();
    A.edit('a1');
    await A.save();
    expect(server.puts[0].base_canvas_version).toBe('c1:v1');
    expect(A.store.getState().canvasVersion).toBe('c1:v2');
    expect(serverLabel()).toBe('a1');
  },

  'salvataggio dopo salvataggio': async () => {
    const A = openTab();
    A.edit('a1'); await A.save();
    A.edit('a2'); await A.save();
    A.edit('a3'); await A.save();
    expect(server.puts.map(p => p.base_canvas_version)).toEqual(['c1:v1', 'c1:v2', 'c1:v3']);
    expect(A.store.getState().saveConflict).toBe(null);
    expect(A.store.getState().canvasVersion).toBe('c1:v4');
  },

  'Valida / Anteprima': async () => {
    // Percorso vero: Valida e Anteprima dal PipelineRunner dell'editor, che
    // salvano prima (Salva e continua) e poi chiedono la validazione al
    // server finto, che (come il backend) non cambia canvas_version.
    const A = openTab(useCanvasStore);
    const onPreview = vi.fn();
    useCanvasStore.getState().setEditorActions({ confirmSaveBeforeRead: async () => true, onPreview });
    render(<PipelineRunner user={{ role: 'user' }} />);
    A.edit('a1');
    await act(async () => { await useCanvasStore.getState().getEditorAction('onValidate')(); });
    expect(validateProjectCanvas).toHaveBeenCalledTimes(1);
    expect(useCanvasStore.getState().canvasVersion).toBe('c1:v2');
    A.edit('a2');
    await act(async () => { await useCanvasStore.getState().getEditorAction('previewWithValidation')(); });
    expect(validateProjectCanvas).toHaveBeenCalledTimes(2);
    expect(onPreview).toHaveBeenCalledWith('o');
    expect(useCanvasStore.getState().canvasVersion).toBe('c1:v3');
    // Il salvataggio dopo parte dal token di Valida/Anteprima: nessun falso conflitto.
    A.edit('a3');
    await A.save();
    expect(server.puts.map(p => p.base_canvas_version)).toEqual(['c1:v1', 'c1:v2', 'c1:v3']);
    expect(useCanvasStore.getState().saveConflict).toBe(null);
    expect(serverLabel()).toBe('a3');
  },

  'salvataggio superato (409)': async () => {
    // Due schede sulla stessa versione: la prima salva, la seconda e' rifiutata.
    const A = openTab();
    const B = openTab();
    A.edit('di-A'); await A.save();
    B.edit('di-B');
    await expect(B.save()).rejects.toMatchObject({ name: 'CanvasVersionConflictError' });
    // Il canvas del server e' quello di A; B resta "da salvare" con l'avviso.
    expect(serverLabel()).toBe('di-A');
    const st = B.store.getState();
    expect(st.saveConflict).toMatchObject({ kind: 'save', serverVersion: 'c1:v2' });
    expect(st.saveStatus).toBe('error');
    expect(st.isDirty).toBe(true);
    expect(st.canvasVersion).toBe('c1:v1');
    // Un altro salvataggio non sovrascrive di nascosto: ancora 409.
    await expect(B.save()).rejects.toMatchObject({ name: 'CanvasVersionConflictError' });
    expect(serverLabel()).toBe('di-A');
  },

  '"Sovrascrivi"': async () => {
    const A = openTab();
    const B = openTab(useCanvasStore);
    A.edit('di-A'); await A.save();
    B.edit('di-B');
    await expect(B.save()).rejects.toBeTruthy();
    await overwriteServer();
    expect(server.puts.at(-1)).not.toHaveProperty('base_canvas_version');
    expect(serverLabel()).toBe('di-B');
    expect(useCanvasStore.getState().saveConflict).toBe(null);
    expect(useCanvasStore.getState().canvasVersion).toBe(server.version);
    // Il salvataggio dopo torna condizionato, sulla versione nuova.
    B.edit('di-B2'); await B.save();
    expect(server.puts.at(-1).base_canvas_version).toBe('c1:v3');
  },

  '"Ricarica la versione del server"': async () => {
    const A = openTab();
    const B = openTab(useCanvasStore);
    A.edit('di-A'); await A.save();
    B.edit('di-B');
    await expect(B.save()).rejects.toBeTruthy();
    expect(reloadServerVersion()).toEqual({ ok: true });
    // Le modifiche di B sono la copia di sicurezza della sua scheda; nessuna scrittura.
    const tabId = sessionStorage.getItem('plenora:tab-id');
    const copy = readDraft('7', 'p1', tabId);
    expect(copy.nodes.find(x => x.id === 's').data.label).toBe('di-B');
    expect(copy.baseUpdatedAt).toBe('2026-11-04T10:00:00Z');
    expect(localStorage.getItem(draftKey('7', 'p1', tabId))).toBeTruthy();
    expect(useCanvasStore.getState().isDirty).toBe(false);
    expect(pageReload.run).toHaveBeenCalledTimes(1);
    expect(serverLabel()).toBe('di-A');
  },

  'ripristino di una versione': async () => {
    vi.useFakeTimers();
    try {
      openTab(useCanvasStore);
      await restoreVersion('ver-9');
      expect(server.restores).toEqual([{ versionId: 'ver-9', base: 'c1:v1' }]);
      expect(useCanvasStore.getState().canvasVersion).toBe('c1:v2');
      vi.advanceTimersByTime(1000);
      expect(pageReload.run).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  },

  'ripristino superato (409)': async () => {
    const A = openTab();
    openTab(useCanvasStore);
    A.edit('di-A'); await A.save();
    await expect(restoreVersion('ver-9')).rejects.toMatchObject({ name: 'CanvasVersionConflictError' });
    expect(useCanvasStore.getState().saveConflict).toMatchObject({ kind: 'restore', versionId: 'ver-9' });
    expect(pageReload.run).not.toHaveBeenCalled();
    expect(serverLabel()).toBe('di-A');
    // "Sovrascrivi" rifa' il ripristino senza base.
    vi.useFakeTimers();
    try {
      await overwriteServer();
      expect(server.restores.at(-1)).toEqual({ versionId: 'ver-9', base: null });
      vi.advanceTimersByTime(1000);
      expect(pageReload.run).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  },

  'progetto senza canvas_version': async () => {
    server.versioned = false;
    const A = openTab(createCanvasStore(), { metadata: { id: 'p1' } });
    expect(A.store.getState().canvasVersion).toBe(null);
    updateCanvas.mockImplementation(async (_pid, body) => { server.puts.push(body); return { status: 'ok' }; });
    try {
      A.edit('a1'); await A.save();
      A.edit('a2'); await A.save();
    } finally {
      updateCanvas.mockReset();
      updateCanvas.mockImplementation(PUT);
    }
    // Nessuna base: scrittura compatibile; il GET conferma che il server non versiona.
    expect(server.puts[0]).not.toHaveProperty('base_canvas_version');
    expect(server.puts[1]).not.toHaveProperty('base_canvas_version');
    expect(A.store.getState().canvasVersion).toBe(null);
  },

  'cambio progetto durante il salvataggio': async () => {
    // Il PUT di A attende le colonne derivate; intanto la scheda apre B.
    let release;
    gate.promise = new Promise(r => { release = r; });
    const A = openTab(useCanvasStore);
    A.edit('di-A');
    const saving = A.save();
    const st = useCanvasStore.getState();
    st.init('p2', 'manipola');
    st.loadGraph([n('s', 'project_source')], [], { canvasVersion: 'p2:v7', updatedAt: null });
    release(null);
    await saving;
    // Progetto, canvas e base dallo snapshot iniziale: A con il token di A.
    expect(updateCanvas).toHaveBeenCalledTimes(1);
    expect(updateCanvas.mock.calls[0][0]).toBe('p1');
    expect(server.puts[0].base_canvas_version).toBe('c1:v1');
    expect(serverLabel()).toBe('di-A');
    // B resta con il suo token, senza conflitti.
    expect(useCanvasStore.getState().canvasVersion).toBe('p2:v7');
    expect(useCanvasStore.getState().saveConflict).toBe(null);
  },

  'risposta senza token, GET della stessa scrittura': async () => {
    // Il PUT riesce ma non porta il token; il GET (stesso updated_at) si'.
    const A = openTab();
    updateCanvas.mockImplementationOnce(async (pid, body) => {
      const rest = withoutToken(await PUT(pid, body));
      return rest;
    });
    A.edit('a1'); await A.save();
    expect(server.gets).toBe(1);
    expect(A.store.getState().canvasVersion).toBe('c1:v2');
    // Il controllo resta acceso: il salvataggio dopo e' condizionato e riesce.
    A.edit('a2'); await A.save();
    expect(server.puts.map(p => p.base_canvas_version)).toEqual(['c1:v1', 'c1:v2']);
    expect(A.store.getState().saveConflict).toBe(null);
  },

  'risposta senza token, GET non dimostra la scrittura': async () => {
    // Il PUT di A non porta il token; prima del GET un'altra scheda salva.
    const A = openTab();
    updateCanvas.mockImplementationOnce(async (pid, body) => {
      const rest = withoutToken(await PUT(pid, body));
      // Un'altra scrittura tra la risposta e il GET.
      write({ nodes: [{ id: 's', label: 'di-altri' }] });
      return rest;
    });
    A.edit('di-A'); await A.save();
    // Il GET descrive un'altra scrittura: si tiene il token precedente.
    expect(A.store.getState().canvasVersion).toBe('c1:v1');
    // Il salvataggio dopo e' rifiutato in modo visibile: mai sovrascrittura silenziosa.
    A.edit('di-A2');
    await expect(A.save()).rejects.toMatchObject({ name: 'CanvasVersionConflictError' });
    expect(A.store.getState().saveConflict).toMatchObject({ kind: 'save' });
    expect(serverLabel()).toBe('di-altri');
  },
};

describe('salvataggio condizionato alla versione: tabella delle transizioni', () => {
  it('ogni riga della tabella in utils/canvasVersion.js ha il suo test (e viceversa)', () => {
    const src = fs.readFileSync(path.join(SRC, 'utils/canvasVersion.js'), 'utf-8');
    const table = src.slice(src.indexOf('TABELLA DELLE TRANSIZIONI'));
    const rows = table.split('\n')
      .map(l => l.match(/^ \* \| ([^|]+?)\s+\|/))
      .filter(Boolean)
      .map(m => m[1].trim())
      .filter(name => name !== 'evento' && !/^-+$/.test(name));
    expect(rows.sort()).toEqual(Object.keys(CANVAS_VERSION_TRANSITIONS).sort());
  });

  Object.entries(CANVAS_VERSION_TRANSITIONS).forEach(([event, run]) => {
    it(`transizione: ${event}`, run);
  });
});

describe('avviso del conflitto', () => {
  it('shows the choices only after a conflict; each choice goes through serverActions', async () => {
    const A = openTab();
    const B = openTab(useCanvasStore);
    render(<SaveConflictNotice />);
    expect(screen.queryByRole('alert')).toBeNull();
    A.edit('di-A'); await A.save();
    B.edit('di-B');
    await act(async () => { await B.save().catch(() => {}); });
    expect(screen.getByRole('alert').textContent).toMatch(/non sono state salvate/);
    expect(screen.getByRole('alert').textContent).toMatch(/copia di sicurezza/);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Sovrascrivi con le mie modifiche/ })); });
    expect(serverLabel()).toBe('di-B');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('read-only: no notice', async () => {
    openTab(useCanvasStore);
    act(() => { useCanvasStore.setState({ saveConflict: { kind: 'save', at: 1 } }); useCanvasStore.getState().setReadOnly(true); });
    render(<SaveConflictNotice />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('enumeration: one conditioned writer', () => {
  it('PUT canvas and version restore are called only by the store, through writeConditioned', () => {
    expect(usersOf(/\bupdateCanvas\(/)).toEqual(['store/useCanvasStore.js']);
    expect(usersOf(/\brestoreCanvasVersion\(/)).toEqual(['store/useCanvasStore.js']);
    const store = code(path.join(SRC, 'store/useCanvasStore.js'));
    expect((store.match(/updateCanvas\(/g) || []).length).toBe(1);
    expect((store.match(/restoreCanvasVersion\(/g) || []).length).toBe(1);
    expect((store.match(/writeConditioned\(\{/g) || []).length).toBe(2);
  });

  it('restoreVersionQuiet and overwriteAfterConflict are used only by serverActions', () => {
    expect(usersOf(/restoreVersionQuiet|overwriteAfterConflict/)).toEqual(['store/useCanvasStore.js', 'utils/serverActions.js']);
  });

  it('every editor opens the project with its server version (loadGraph(..., serverVersionOf(project)))', () => {
    ['pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx'].forEach(f => {
      const calls = code(path.join(SRC, f)).match(/\bloadGraph\([^;]*\);/g) || [];
      expect(calls.length).toBe(1);
      expect(calls[0]).toMatch(/serverVersionOf\(project\)\);$/);
    });
  });
});
