import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';

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
  'api/http.js': 'flag "avviso di versione gia\' mostrato" (diagnostica, uguale per tutti)',
  'api/openapiClient.js': 'schema OpenAPI pubblico del server, uguale per ogni utente',
  'components/canvas/compactNodeTypes.js': 'WeakMap con chiave i tipi di nodo: muore con gli oggetti',
  'context/ToastContext.jsx': 'contatore degli id dei toast',
  'hooks/useTheme.js': 'tema chiaro/scuro: preferenza del dispositivo, non dell\'account',
  'utils/authBus.js': 'iscritti agli eventi di uscita, non dati',
  'utils/estraiJoins.js': 'contatore degli id degli archi e WeakMap con chiave gli oggetti',
  'utils/glossary.js': 'WeakMap con chiave gli archi: muore con gli oggetti',
  'utils/localDrafts.js': 'id della scheda e flag di pulizia: le copie locali sono per utente e restano apposta',
  'utils/errorReporting.js': 'client di Sentry registrato all\'avvio: uguale per ogni utente',
  'utils/session.js': 'il registro stesso',
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

  it('resetSession svuota canvas, colonne e registry delle trasformazioni', async () => {
    const { resetSession, registeredSessionResets } = await import('../../src/utils/session');
    const { default: useCanvasStore } = await import('../../src/store/useCanvasStore');
    const { default: useColumnStore } = await import('../../src/store/useColumnStore');
    const transformRegistry = await import('../../src/utils/transformRegistry');

    expect(registeredSessionResets()).toEqual(expect.arrayContaining(['canvasStore', 'columnStore', 'transformRegistry']));

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

  it('un errore di uno svuotamento non ferma gli altri', async () => {
    const { resetSession, registerSessionReset } = await import('../../src/utils/session');
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
