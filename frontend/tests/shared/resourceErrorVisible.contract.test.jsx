/**
 * Contract: con status 'error' l'errore di una risorsa e' SEMPRE visibile
 * (Fase 5, correzione dopo la conferma di T2+T4, punti 3+4).
 *
 *  - hooks/useResource: i dati sotto l'errore sono solo quelli della STESSA
 *    chiave (`stale`); con keepPreviousData e chiave cambiata un errore non
 *    lascia i dati della chiave di prima;
 *  - ListState e <ResourceError resource>: senza dati errore pieno; con dati
 *    avviso «Dati non aggiornati» + Riprova sopra i dati;
 *  - enumerazione statica: OGNI `const x = useResource(` di src/ o passa
 *    `resource={x}` (ListState, ResourceError o un componente che lo fa) o
 *    e' nella tabella DECLARED con il modo in cui mostra l'errore; vietati
 *    `x.status === 'error' && !x.hasData ? (` e `error && !x.hasData ? (`
 *    (errore solo senza dati) e `message={x.error}` (si passa la risorsa);
 *  - rendering: LogViewer (filtro cambiato con errore: niente righe del
 *    filtro di prima) e AdminBackupsPage («Aggiorna» con errore: avviso sopra
 *    la lista e le schede).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, renderHook, act, waitFor, fireEvent, cleanup, within } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

const { httpMock, toastMock, backups } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  backups: { listBackups: vi.fn(), getBackupStatus: vi.fn(), getBackupSchedule: vi.fn(), triggerBackup: vi.fn() },
}));
vi.mock('../../src/api/http', () => ({ default: httpMock }));
vi.mock('../../src/api/adminBackups', () => backups);
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: { role: 'superadmin', username: 'root', group_id: 'g' }, logout: vi.fn() }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn(), useSearchParams: () => [new URLSearchParams(), vi.fn()] };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/logviewer/RunsTab', () => ({ default: () => null }));

import { useResource } from '../../src/hooks/useResource';
import ListState, { ResourceError, STALE_TITLE } from '../../src/components/common/ListState';
import LogViewer from '../../src/pages/LogViewer';
import AdminBackupsPage from '../../src/pages/AdminBackupsPage';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

// ---------------------------------------------------------------- hook ---
describe('useResource: dati sotto l\'errore solo della stessa chiave', () => {
  it('stessa chiave, ricarica non riuscita: stale con i dati di prima', async () => {
    let fail = false;
    const { result } = renderHook(() => useResource(() => (fail ? Promise.reject(new Error('giu')) : Promise.resolve(['a'])), { key: 'k' }));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    fail = true;
    await act(async () => { await result.current.reload(); });
    expect(result.current.status).toBe('error');
    expect(result.current.stale).toBe(true);
    expect(result.current.data).toEqual(['a']);
  });

  it('keepPreviousData, chiave cambiata e errore: niente dati della chiave di prima', async () => {
    const b = deferred();
    const { result, rerender } = renderHook(({ k }) => useResource(() => (k === 1 ? Promise.resolve(['filtro 1']) : b.promise), { key: k, keepPreviousData: true }), { initialProps: { k: 1 } });
    await waitFor(() => expect(result.current.data).toEqual(['filtro 1']));
    rerender({ k: 2 });
    expect(result.current.data).toEqual(['filtro 1']); // durante la richiesta si', come prima
    await act(async () => { b.reject(new Error('giu')); });
    expect(result.current.status).toBe('error');
    expect(result.current.data).toBeUndefined();
    expect(result.current.stale).toBe(false);
  });

  it('keepPreviousData: un errore sulla stessa chiave (Live, Aggiorna) resta stale', async () => {
    let fail = false;
    const { result } = renderHook(() => useResource(() => (fail ? Promise.reject(new Error('giu')) : Promise.resolve(['p'])), { key: 'x', keepPreviousData: true }));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    fail = true;
    await act(async () => { await result.current.reload(); });
    expect(result.current.stale).toBe(true);
    expect(result.current.data).toEqual(['p']);
  });
});

// ---------------------------------------------------------- componenti ---
describe('ListState e ResourceError: errore sempre visibile', () => {
  const res = (over) => ({ status: 'ready', data: ['riga'], error: null, stale: false, reload: vi.fn(), ...over });

  it('ResourceError resource: nulla senza errore', () => {
    const { container } = render(<ResourceError resource={res()} title="X" />);
    expect(container.textContent).toBe('');
  });

  it('errore senza dati: errore pieno con Riprova', () => {
    const r = res({ status: 'error', data: undefined, error: 'Server giu' });
    render(<ListState resource={r} errorTitle="Righe non caricate">{(d) => <p>{d.join()}</p>}</ListState>);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/Righe non caricate.*Server giu/);
    fireEvent.click(within(alert).getByRole('button', { name: 'Riprova' }));
    expect(r.reload).toHaveBeenCalled();
  });

  it('errore con dati: «Dati non aggiornati» + Riprova SOPRA i dati', () => {
    const r = res({ status: 'error', error: 'Server giu', stale: true });
    const { container } = render(<ListState resource={r} errorTitle="Righe non caricate">{(d) => <p>{d.join()}</p>}</ListState>);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain(STALE_TITLE);
    expect(alert.textContent).toMatch(/Righe non caricate: Server giu/);
    expect(within(alert).getByRole('button', { name: 'Riprova' })).toBeTruthy();
    expect(screen.getByText('riga')).toBeTruthy();
    expect(container.firstChild).toBe(alert);
  });
});

// ------------------------------------------------- enumerazione statica ---
const SRC = path.resolve(import.meta.dirname, '../../src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
  const full = path.join(dir, e.name);
  if (e.isDirectory()) return walk(full);
  return /\.(jsx?|tsx?)$/.test(e.name) ? [full] : [];
});
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
const strip = (code) => code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const FILES = walk(SRC).map(f => ({ file: rel(f), code: strip(fs.readFileSync(f, 'utf-8')) }))
  .filter(({ file }) => file !== 'hooks/useResource.js');

// Risorse che non passano `resource={x}`: come mostrano l'errore (sempre,
// anche con dati gia' caricati). Una risorsa nuova non elencata e senza
// `resource={x}` fa fallire il test; una voce che non esiste piu' anche.
const DECLARED = {
  'components/AppSidebar.jsx': { countsResource: 'contatore dei ticket nel menu: con errore nessun numero (la pagina Ticket mostra l\'errore)' },
  'components/arricchisci/ConnectionSelectorModal.jsx': { resource: '`error ?` al posto della lista, prima di ogni dato' },
  'components/arricchisci/TemplateLibraryModal.jsx': { resource: '`error ?` al posto della lista, prima di ogni dato' },
  'components/modals/ColumnBuilderModal.jsx': {
    catalogResource: '`error ?` al posto del catalogo',
    translation: 'translationError mostrato sotto l\'SQL, sempre',
    sampleResource: 'sample.status error mostrato nel riquadro del campione',
  },
  'components/modals/RunHistoryModal.jsx': {
    runsResource: 'runsError sempre sopra la lista',
    nodeResource: 'nodeDataError a caricamento finito',
    compareResource: 'compareError sempre',
  },
  'components/modals/ScheduleWizard.jsx': { formResource: 'toast d\'errore; in modifica la finestra si chiude (niente form inventato)' },
  'components/nodes/CaricanArcGISNode.jsx': {
    connsResource: 'arcgisError: la prima risorsa in errore, sempre, con Riprova',
    servicesResource: 'arcgisError', layersResource: 'arcgisError', foldersResource: 'arcgisError', schemaResource: 'arcgisError',
  },
  'components/nodes/DataSourceNode.jsx': {
    browser: 'browserError sempre nel navigatore dei file',
    schemaResource: 'schemaError al posto delle colonne, con Riprova',
  },
  'components/modals/ExecutionModal.jsx': { meta: 'dato secondario di «Segnala»: senza, il ticket usa categorie e gravità predefinite' },
  'components/nodes/FeatureLayerNode.jsx': { schemaResource: 'avviso sopra i campi (non aggiornati / non caricati) con Riprova' },
  'components/nodes/FileSourceNode.jsx': { resource: '`error &&` sempre' },
  'components/nodes/ProjectSourceNode.jsx': { resource: '`error &&` sempre' },
  'components/nodes/carica/RestDestinationSection.jsx': { resource: 'connectors.status error nel suggerimento, con Riprova' },
  'components/nodes/carica/useCaricaSchemaSelect.js': {
    schemasResource: 'schemasError -> DbDestinationSection, sempre, con Riprova',
    tablesResource: 'tablesError -> DbDestinationSection, con Riprova',
  },
  'components/panels/ApiEnrichNode.jsx': { apiResource: '`status === error &&` sempre, con Riprova' },
  'components/panels/FeatureServicePanel.jsx': {
    connsResource: '`error` della vista corrente sempre sopra la lista, con Riprova',
    servicesResource: 'come sopra', layersResource: 'come sopra',
  },
  'components/projects/useProjectListData.js': { resource: 'esposta come `resource`: ProjectList la mostra con <ResourceError resource>' },
  'components/shared/AllowedGroupsSelector.jsx': { resource: '`error` sempre nel selettore' },
  'hooks/useEditorData.js': {
    boot: 'senza dati: pagina d\'errore dell\'editor (`error`); con dati: avviso (toast) "Progetto non ricaricato"',
    catalogResource: 'catalogError -> EstraiEditor, al posto delle tabelle, con Riprova',
    sourcesResource: 'sourcesError -> EditorSidebar, sempre, con Riprova',
  },
  'hooks/useProjectNames.js': { resource: 'dato secondario (nomi dei progetti nel Log Viewer): con errore restano gli id, interi' },
  'hooks/useRuntimeInputDefinitions.js': { sets: 'connection_options_error nella definizione: BeforeRunModal lo mostra sotto la scelta' },
  'hooks/useTransformRegistry.js': { resource: 'restituita ai componenti: TransformNode (ResourceError resource), ManipolaEditor (avviso della palette), ProjectInputNode (Riprova)' },
  'pages/DataSourceManager.jsx': { resource: 'banner d\'errore sempre, con Riprova', browser: 'browserError sempre' },
  'pages/FileManager.jsx': { resource: 'banner d\'errore sempre, con Riprova' },
  'pages/OpenProjectPage.jsx': { resource: 'pagina "Impossibile aprire il progetto"' },
  'pages/ScheduleList.jsx': { health: 'banner "pianificatore non raggiungibile"' },
  'pages/UserManager.jsx': { summaryResource: 'avviso del riepilogo con Riprova; conferma bloccata' },
};

describe('enumerazione: ogni useResource mostra il suo errore', () => {
  const found = [];
  for (const { file, code } of FILES) {
    // UI-1: anche useSharedResource (stessa risorsa di useResource).
    for (const m of code.matchAll(/(?:const|let)\s+(\w+)\s*=\s*use(?:Shared)?Resource\(/g)) found.push({ file, name: m[1], code });
  }

  it('trova i consumatori', () => {
    expect(found.length).toBeGreaterThan(40);
  });

  it('ogni risorsa passa resource={x} o e\' dichiarata con il modo in cui mostra l\'errore', () => {
    const missing = found
      .filter(({ file, name, code }) => !new RegExp(`resource=\\{${name}\\}`).test(code) && !DECLARED[file]?.[name])
      .map(({ file, name }) => `${file}: ${name}`);
    expect(missing).toEqual([]);
  });

  it('nessuna voce dichiarata e\' superata (risorsa sparita o ora con resource=)', () => {
    const stale = [];
    for (const [file, names] of Object.entries(DECLARED)) {
      for (const [name, reason] of Object.entries(names)) {
        const hit = found.find(f => f.file === file && f.name === name);
        if (!hit || new RegExp(`resource=\\{${name}\\}`).test(hit.code) || !reason) stale.push(`${file}: ${name}`);
      }
    }
    expect(stale).toEqual([]);
  });

  it('vietato mostrare l\'errore solo senza dati, o passare x.error invece della risorsa', () => {
    const offenders = [];
    for (const { file, code } of FILES) {
      if (/(\w+)\.status === 'error' && !\1\.hasData \? \(/.test(code)) offenders.push(`${file}: status error && !hasData ? (`);
      if (/\berror && !\w+\.hasData \? \(/.test(code)) offenders.push(`${file}: error && !hasData ? (`);
      for (const { name } of found.filter(f => f.file === file)) {
        if (new RegExp(`message=\\{${name}\\.error\\}`).test(code)) offenders.push(`${file}: message={${name}.error}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

// ------------------------------------------------------------ rendering ---
const ENTRIES = [{ ts: '2026-04-15T10:00:00.000Z', level: 'INFO', module: 'estrai', op: 'execute', msg: 'Riga del filtro di prima' }];

describe('LogViewer: errore sempre visibile', () => {
  it('filtro cambiato con errore: errore pieno e nessuna riga del filtro di prima', async () => {
    let fail = false;
    httpMock.get.mockImplementation((url) => {
      if (url === '/logs/stats') return Promise.resolve({ data: { total: 1, by_level: { INFO: 1 } } });
      if (url === '/logs') return fail ? Promise.reject(Object.assign(new Error('x'), { response: { status: 500, data: {} } })) : Promise.resolve({ data: { entries: ENTRIES, total: 1, has_more: false } });
      return Promise.resolve({ data: {} });
    });
    render(<LogViewer />);
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Log Tecnici/i })); });
    expect(await screen.findByText('Riga del filtro di prima')).toBeTruthy();
    fail = true;
    globalThis.__frontendConsolePolicy.allowError = true;
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await act(async () => { fireEvent.click(screen.getByText('Errori').closest('button')); });
    await waitFor(() => expect(screen.getAllByRole('alert').some(a => /Log non caricati/.test(a.textContent))).toBe(true));
    expect(screen.queryByText('Riga del filtro di prima')).toBeNull();
    expect(screen.queryByText(STALE_TITLE)).toBeNull();
  });
});

describe('AdminBackupsPage: «Aggiorna» con errore', () => {
  it('avviso «Dati non aggiornati» sopra la lista e nelle schede, con i dati di prima', async () => {
    backups.listBackups.mockResolvedValue({ items: [{ key: 'backup-1.sql.gz', size_mb: 1, storage_class: 'STANDARD', last_modified: '2026-11-01T00:00:00Z' }], count: 1, bucket: 'b' });
    backups.getBackupStatus.mockResolvedValue({ result: 'success', finished_at: '2026-11-01' });
    backups.getBackupSchedule.mockResolvedValue({ timer_active: true, next_run: 'domani' });
    render(<AdminBackupsPage />);
    expect(await screen.findByText('backup-1.sql.gz')).toBeTruthy();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const boom = () => Promise.reject(Object.assign(new Error('x'), { response: { status: 500, data: {} } }));
    backups.listBackups.mockImplementation(boom);
    backups.getBackupStatus.mockImplementation(boom);
    backups.getBackupSchedule.mockImplementation(boom);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Aggiorna/ })); });
    await waitFor(() => expect(screen.getAllByText(STALE_TITLE).length).toBe(3));
    const texts = screen.getAllByRole('alert').map(a => a.textContent);
    expect(texts.some(t => /Lista dei backup non caricata/.test(t))).toBe(true);
    expect(texts.some(t => /Stato non caricato/.test(t))).toBe(true);
    expect(texts.some(t => /Pianificazione non caricata/.test(t))).toBe(true);
    expect(screen.getByText('backup-1.sql.gz')).toBeTruthy();
  });
});
