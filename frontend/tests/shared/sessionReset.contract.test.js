import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';
// Import STATICI in testa (punto 13 della correzione dopo la conferma): un
// import() a freddo nel corpo del test, sotto carico, superava i 5 s.
import { resetSession, registeredSessionResets, registerSessionReset } from '../../src/utils/session';
import useCanvasStore from '../../src/store/useCanvasStore';
import useColumnStore from '../../src/store/useColumnStore';
import * as transformRegistry from '../../src/utils/transformRegistry';
import * as http401 from '../../src/api/http';
import * as connectionSets from '../../src/utils/connectionSets';
import { listConnectionSets } from '../../src/api/connections';

vi.mock('../../src/api/connections', () => ({ listConnectionSets: vi.fn() }));

/**
 * Contract della fine sessione (Fase 5 T1: R5.4, R5.5, R1-N4, R5-N3).
 *
 *  1. Enumerazione: ogni file di src/ con stato di modulo (let di modulo,
 *     Map/Set/WeakMap vuote di modulo, store zustand) o si registra in
 *     utils/session (registerSessionReset) o ha un motivo scritto qui per
 *     non contenere dati dell'utente. Una cache nuova non registrata fa
 *     fallire il test.
 *  2. Funzionamento: resetSession() svuota davvero store e cache.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'mocks' ? [] : collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}
const rel = (p) => path.relative(srcRoot, p).split(path.sep).join('/');

const MODULE_STATE = [
  /^let [A-Za-z_$]/m,
  /^(export )?const [A-Za-z_$][\w$]* = new (Map|Set|WeakMap|WeakSet)\(\s*\)/m,
  /from 'zustand'[\s\S]*\bcreate\(/,
];

// Stato di modulo che NON contiene dati dell'utente, col motivo.
const NOT_USER_DATA = {
  'hooks/useTransformRegistry.js': 'insieme dei setter dei componenti montati (avviso quando il catalogo arriva): nessun dato dell utente, il catalogo vive in utils/transformRegistry che e registrato',
  'components/canvas/compactNodeTypes.js': 'WeakMap con chiave i tipi di nodo: muore con gli oggetti',
  'components/canvas/CompactNode.jsx': 'WeakMap dell\'indice delle maniglie con chiave gli array degli archi (UI-2): muore con gli oggetti',
  'utils/format.js': 'formattatori Intl per opzioni (locale fissa it-IT): nessun dato dell\'utente',
  'context/ToastContext.jsx': 'contatore degli id dei toast',
  'hooks/useTheme.js': 'tema chiaro/scuro: preferenza del dispositivo, non dell\'account',
  'utils/authBus.js': 'iscritti agli eventi di uscita, non dati',
  'utils/estraiJoins.js': 'contatore degli id degli archi e WeakMap con chiave gli oggetti',
  'utils/glossary.js': 'WeakMap con chiave gli archi: muore con gli oggetti',
  'utils/localDrafts.js': 'id della scheda e flag di pulizia: le copie locali sono per utente e restano apposta',
  'utils/errorReporting.js': 'client di Sentry registrato all\'avvio: uguale per ogni utente',
  'utils/session.js': 'il registro stesso',
  'utils/dialogStack.js': 'stack delle finestre aperte (Fase 5, T4): si svuota smontandole, nessun dato',
  'hooks/useUnsavedChangesGuard.js': 'contatore degli editor montati (Fase 5, T4): si azzera smontandoli, nessun dato',
  'store/typingAction.js': 'contatore delle sessioni di fuoco, WeakMap con chiave i campi e ultimo evento di digitazione (Fase 5, T5+T6): nessun dato dell utente',
};

describe('fine sessione: registro unico (resetSession)', () => {
  it('ogni stato di modulo e\' registrato o dichiarato non dell\'utente', () => {
    const violations = [];
    const seen = new Set();
    for (const file of collectSources(srcRoot)) {
      const name = rel(file);
      const content = fs.readFileSync(file, 'utf-8');
      if (!MODULE_STATE.some((re) => re.test(content))) continue;
      seen.add(name);
      if (content.includes('registerSessionReset(') || name in NOT_USER_DATA) continue;
      violations.push(`${name}: stato di modulo non registrato in utils/session`);
    }
    // Nessuna voce dell'elenco senza piu' stato (l'elenco resta esatto).
    for (const name of Object.keys(NOT_USER_DATA)) {
      if (!seen.has(name)) violations.push(`${name}: in NOT_USER_DATA ma senza stato di modulo`);
    }
    expect(violations).toEqual([]);
  });

  it('il logout passa da resetSession e non da ascoltatori sparsi', () => {
    const auth = fs.readFileSync(path.join(srcRoot, 'context/AuthContext.jsx'), 'utf-8');
    expect(auth).toMatch(/resetSession\(\)/);
    const stray = collectSources(srcRoot)
      .filter((file) => /addLogoutListener|emitLogout\(/.test(fs.readFileSync(file, 'utf-8')))
      .map(rel);
    expect(stray).toEqual([]);
  });

  it('resetSession svuota canvas, colonne e registry delle trasformazioni', () => {
    expect(http401).toBeTruthy();
    expect(registeredSessionResets()).toEqual(expect.arrayContaining(['canvasStore', 'columnStore', 'transformRegistry', 'connectionSets', 'http401']));

    useCanvasStore.setState({ nodes: [{ id: 'n1', data: {} }], edges: [], projectId: 'p-utente-A', isReadOnly: false });
    useColumnStore.setState({ projectId: 'p-utente-A', schemas: { n1: [{ name: 'segreto' }] }, status: 'ok' });

    resetSession();

    expect(useCanvasStore.getState().nodes).toEqual([]);
    expect(useCanvasStore.getState().projectId).toBeNull();
    expect(useCanvasStore.getState().isReadOnly).toBe(true);
    expect(useColumnStore.getState().schemas).toEqual({});
    expect(useColumnStore.getState().projectId).toBeNull();
    expect(transformRegistry.getCachedTransformRegistry()).toBeNull();
  });

  it('un errore di uno svuotamento non ferma gli altri', () => {
    const calls = [];
    registerSessionReset('test-rotto', () => { throw new Error('rotto'); });
    registerSessionReset('test-ok', () => calls.push('ok'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    resetSession();
    spy.mockRestore();
    registerSessionReset('test-rotto', () => {});
    registerSessionReset('test-ok', () => {});
    expect(calls).toEqual(['ok']);
  });
});

describe('set di connessioni: risorsa condivisa della sessione (punto 12)', () => {
  it('la risposta condivisa scade dopo CACHE_TTL_MS: set creati altrove compaiono (rilettura R3)', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-05T10:00:00Z') });
    try {
      connectionSets.invalidateConnectionSets();
      listConnectionSets.mockReset();
      listConnectionSets.mockResolvedValue({ connection_sets: [{ id: 'vecchio' }] });
      await connectionSets.loadSharedConnectionSets();
      listConnectionSets.mockResolvedValue({ connection_sets: [{ id: 'nuovo-altrove' }] });
      vi.setSystemTime(Date.now() + connectionSets.CACHE_TTL_MS - 1);
      expect((await connectionSets.loadSharedConnectionSets())[0].id).toBe('vecchio');
      vi.setSystemTime(Date.now() + 2);
      expect((await connectionSets.loadSharedConnectionSets())[0].id).toBe('nuovo-altrove');
      expect(listConnectionSets).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('N letture contemporanee = UNA richiesta; resetSession la svuota', async () => {
    connectionSets.invalidateConnectionSets();
    listConnectionSets.mockReset();
    listConnectionSets.mockResolvedValue({ connection_sets: [{ id: 'cs-A' }] });
    const results = await Promise.all([1, 2, 3, 4, 5].map(() => connectionSets.loadSharedConnectionSets()));
    expect(listConnectionSets).toHaveBeenCalledTimes(1);
    expect(results.every((r) => r[0].id === 'cs-A')).toBe(true);
    await connectionSets.loadSharedConnectionSets();
    expect(listConnectionSets).toHaveBeenCalledTimes(1);

    resetSession();
    listConnectionSets.mockResolvedValue({ connection_sets: [{ id: 'cs-B' }] });
    expect((await connectionSets.loadSharedConnectionSets())[0].id).toBe('cs-B');
    expect(listConnectionSets).toHaveBeenCalledTimes(2);
  });

  it('una risposta arrivata dopo il reset non riempie la sessione nuova; un errore non resta', async () => {
    connectionSets.invalidateConnectionSets();
    listConnectionSets.mockReset();
    let release;
    listConnectionSets.mockReturnValueOnce(new Promise((r) => { release = r; }));
    const late = connectionSets.loadSharedConnectionSets();
    resetSession();
    release({ connection_sets: [{ id: 'utente-precedente' }] });
    await late;
    listConnectionSets.mockRejectedValueOnce(new Error('rete'));
    await expect(connectionSets.loadSharedConnectionSets()).rejects.toThrow('rete');
    listConnectionSets.mockResolvedValueOnce({ connection_sets: [{ id: 'nuovo' }] });
    expect((await connectionSets.loadSharedConnectionSets())[0].id).toBe('nuovo');
  });

  it('i nodi e la finestra usano la risorsa condivisa, non la richiesta diretta', () => {
    for (const file of ['components/nodes/ProjectInputNode.jsx', 'hooks/useRuntimeInputDefinitions.js']) {
      const content = fs.readFileSync(path.join(srcRoot, file), 'utf-8');
      // Fase 5 T2: passata a useResource come caricatore (stessa risorsa condivisa).
      expect(content).toMatch(/loadSharedConnectionSets\(|useResource\(loadSharedConnectionSets/);
      expect(content).not.toContain('listConnectionSets(');
    }
    expect(fs.readFileSync(path.join(srcRoot, 'components/connections/ConnectionSetsPanel.jsx'), 'utf-8')).toContain('invalidateConnectionSets()');
  });
});
