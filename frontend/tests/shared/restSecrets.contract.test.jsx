import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn(async () => ({ status: 'ok' })),
  getProject: vi.fn(async () => ({ metadata: {}, canvas_version: null })),
  validateProjectCanvas: vi.fn(async () => ({ valid: true, has_errors: false, issues: [] })),
}));
vi.mock('../../src/api/apiConnections', () => ({
  listApiConnections: vi.fn(async () => [{ id: 'api-1', name: 'Gestionale' }]),
}));

import { updateCanvas } from '../../src/api/projects';
import useCanvasStore from '../../src/store/useCanvasStore';
import PipelineRunner from '../../src/components/editor/PipelineRunner';
import RestDestinationSection from '../../src/components/nodes/carica/RestDestinationSection';
import { deserializeNodes } from '../../src/utils/nodeFactories';
import { persistedNodeData } from '../../src/utils/nodeTypeRegistry';
import {
  SECRET_HEADER_NAMES, NON_SECRET_HEADER_NAMES, isSecretHeader, redactRestHeaders, restMigrationIssues,
} from '../../src/utils/restSecrets';

/**
 * Contract R10.8 (Fase 5 T1) e correzione dopo la conferma (punto 2).
 * REGOLA: niente si toglie in silenzio. Le intestazioni segrete di un canvas
 * vecchio restano in memoria e il nodo e' "da migrare": avviso nel nodo e
 * nella finestra «Prima di avviare»; salvataggio ed esecuzione bloccati con
 * un messaggio chiaro finche' l'utente non sceglie un connettore o rimuove
 * la riga. I nomi di credenziale sono un elenco dichiarato.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(srcRoot, rel), 'utf-8');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

const LEGACY_HEADERS = [
  { name: 'X-Legacy', value: 'shh', secret: true },
  { name: 'Authorization', value: 'Bearer abc' },
  { name: 'X-Session-Id', value: 'sess-1' },
  { name: 'X-Client', value: 'plenora' },
];
const caricaNode = (headers = LEGACY_HEADERS, extra = {}) => ({
  id: 'c1',
  type: 'carica',
  position: { x: 0, y: 0 },
  data: { dest_type: 'rest', rest_url: 'https://api.example.com/items', rest_headers: headers, ...extra },
});

describe('nomi di credenziale: regola larga, eccezioni dichiarate', () => {
  it('riconosce i nomi noti, ogni nome con una parola di credenziale e le righe marcate secret', () => {
    for (const name of [...SECRET_HEADER_NAMES, 'Authorization', 'Proxy-Authorization', 'X-API-Key', 'Api-Key', 'Cookie', 'Set-Cookie', 'X-Auth-Token', 'X-Access-Token',
      // Rilettura T1+T3 (S1): nomi comuni che l'elenco ristretto lasciava in chiaro.
      'apikey', 'api_key', 'X-Goog-Api-Key', 'Ocp-Apim-Subscription-Key', 'X-Api-Secret', 'X-Client-Secret', 'X-Access-Key', 'X-Password', 'X-Functions-Key', 'Token',
      'X-Amz-Security-Token', 'X-Hub-Signature', 'X-JWT']) {
      expect(isSecretHeader({ name, value: 'x' }), name).toBe(true);
    }
    expect(isSecretHeader({ name: 'X-Qualsiasi', value: 'x', secret: true })).toBe(true);
  });

  it('NON tocca i nomi dichiarati non segreti e quelli senza parole di credenziale', () => {
    for (const name of [...Object.keys(NON_SECRET_HEADER_NAMES), 'X-Session-Id', 'X-Request-Id', 'X-Client', 'Content-Type', 'Accept', 'User-Agent', 'X-Correlation-Id']) {
      expect(isSecretHeader({ name, value: 'x' }), name).toBe(false);
    }
  });

  it('ogni eccezione dichiara il motivo', () => {
    for (const [name, reason] of Object.entries(NON_SECRET_HEADER_NAMES)) {
      expect(name).toBe(name.toLowerCase());
      expect(String(reason).length).toBeGreaterThan(10);
    }
  });
});

describe('al caricamento niente si toglie: il nodo e\' da migrare', () => {
  it('persistedNodeData e deserializeNodes conservano le intestazioni', () => {
    expect(persistedNodeData(caricaNode()).rest_headers).toEqual(LEGACY_HEADERS);
    const [loaded] = deserializeNodes([{ id: 'c1', type: 'carica', position: { x: 0, y: 0 }, dest_type: 'rest', rest_headers: LEGACY_HEADERS }]);
    expect(loaded.data.rest_headers).toEqual(LEGACY_HEADERS);
    expect(restMigrationIssues([loaded])).toEqual([{ nodeId: 'c1', headers: ['X-Legacy', 'Authorization'] }]);
  });

  it('la copia locale non porta i valori ma conserva la riga (resta da migrare)', () => {
    const redacted = redactRestHeaders(LEGACY_HEADERS);
    expect(JSON.stringify(redacted)).not.toContain('Bearer abc');
    expect(JSON.stringify(redacted)).not.toContain('shh');
    expect(redacted.map((h) => h.name)).toEqual(LEGACY_HEADERS.map((h) => h.name));
    expect(redacted.find((h) => h.name === 'X-Session-Id').value).toBe('sess-1');
    expect(restMigrationIssues([caricaNode(redacted)])[0].headers).toEqual(['X-Legacy', 'Authorization']);
    expect(read('hooks/useLocalDraft.js')).toMatch(/redactRestHeaders\(/);
  });
});

describe('salvataggio ed esecuzione bloccati finche\' non si migra', () => {
  beforeEach(() => {
    updateCanvas.mockClear();
    const st = useCanvasStore.getState();
    st.reset();
    st.setReadOnly(false);
    st.init('p1', 'manipola');
  });

  it('il salvataggio non parte e dice perche\'', async () => {
    const st = useCanvasStore.getState();
    st.loadGraph([caricaNode()], [], { canvasVersion: 'c1:a' });
    st.updateNodeData('c1', { rest_method: 'POST' });
    await expect(useCanvasStore.getState().handleSaveQuiet()).rejects.toThrow(/da migrare.*Authorization/);
    expect(updateCanvas).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().saveStatus).toBe('error');
    expect(useCanvasStore.getState().saveError).toMatch(/connettore API/);
  });

  it('dopo la rimozione esplicita delle righe segrete si salva senza credenziali', async () => {
    const st = useCanvasStore.getState();
    st.loadGraph([caricaNode()], [], { canvasVersion: 'c1:a' });
    st.updateNodeData('c1', { rest_headers: LEGACY_HEADERS.filter((h) => !isSecretHeader(h)) });
    await useCanvasStore.getState().handleSaveQuiet();
    expect(updateCanvas).toHaveBeenCalledTimes(1);
    const sent = JSON.stringify(updateCanvas.mock.calls[0][1]);
    expect(sent).not.toContain('Bearer abc');
    expect(sent).toContain('X-Session-Id');
  });

  it('Esegui apre «Prima di avviare» con l\'avviso e Avvia spento, senza salvare', async () => {
    const st = useCanvasStore.getState();
    st.loadGraph([caricaNode(), { id: 'o', type: 'result', position: { x: 0, y: 0 }, data: {} }], [], { canvasVersion: 'c1:a' });
    const onExecute = vi.fn();
    st.setEditorActions({ onExecute });
    render(<PipelineRunner user={{ role: 'user' }} />);
    await act(async () => { await useCanvasStore.getState().getEditorAction('runPipeline')(); });
    expect(screen.getByText('Credenziali da migrare')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Avvia l'esecuzione/ }).disabled).toBe(true);
    expect(updateCanvas).not.toHaveBeenCalled();
    expect(onExecute).not.toHaveBeenCalled();
  });
});

describe('nel nodo: avviso e migrazione esplicita', () => {
  const renderSection = (headers, onMultiUpdate = vi.fn(), onUpdate = vi.fn()) => {
    render(
      <RestDestinationSection
        restUrl="https://api.example.com/items" restMethod="POST" restBatchMode="batch" restBatchSize={10}
        restTimeoutSeconds={30} restHeaders={headers} restAuthApiId="" restBodyMapping={[]}
        availableColumns={[]} showMapping={false} onToggleMapping={() => {}} showAdvanced={false}
        onToggleAdvanced={() => {}} onUpdate={onUpdate} onMultiUpdate={onMultiUpdate}
      />,
    );
    return { onMultiUpdate, onUpdate };
  };

  it('l\'avviso "da migrare" e\' sempre visibile, anche con le intestazioni chiuse', async () => {
    renderSection(LEGACY_HEADERS);
    expect(screen.getByTestId('rest-secrets-migrate').textContent).toMatch(/X-Legacy, Authorization/);
    await screen.findByText('Connettore API: Gestionale');
  });

  it('scegliere un connettore sostituisce le righe segrete in una sola modifica', async () => {
    const { onMultiUpdate } = renderSection(LEGACY_HEADERS);
    await screen.findByText('Connettore API: Gestionale');
    fireEvent.change(screen.getByLabelText('Autenticazione'), { target: { value: 'api-1' } });
    expect(onMultiUpdate).toHaveBeenCalledWith({
      rest_auth_api_id: 'api-1',
      rest_headers: LEGACY_HEADERS.filter((h) => !isSecretHeader(h)),
    });
  });

  it('nessun componente marca un\'intestazione come segreta (niente "Segreto" nel canvas)', () => {
    const offenders = collectSources(path.join(srcRoot, 'components'))
      .filter((file) => /\bsecret\s*:\s*(true|false)|'secret'/.test(fs.readFileSync(file, 'utf-8')))
      .map((file) => path.relative(srcRoot, file));
    expect(offenders).toEqual([]);
  });
});

describe('devtools solo in sviluppo', () => {
  it('ogni devtools(...) di zustand e\' spento in produzione', () => {
    const offenders = [];
    for (const file of collectSources(srcRoot)) {
      const content = fs.readFileSync(file, 'utf-8');
      if (!/\bdevtools\s*\(/.test(content)) continue;
      // FRONTEND_ENV.isDev = import.meta.env.DEV (config/runtimeEnv, unico confine delle env).
      if (!/enabled:\s*FRONTEND_ENV\.isDev\b/.test(content)) offenders.push(path.relative(srcRoot, file));
    }
    expect(offenders).toEqual([]);
  });

  it('nessun altro devtools di librerie di stato (react-query non e\' una dipendenza)', () => {
    const pkg = JSON.parse(fs.readFileSync(path.resolve(srcRoot, '../package.json'), 'utf-8'));
    const deps = { ...pkg.dependencies };
    expect(Object.keys(deps).filter((name) => /react-query|redux-devtools|devtools/.test(name))).toEqual([]);
  });
});
