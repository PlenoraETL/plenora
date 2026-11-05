import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { can } from '../../src/utils/permissions';

/**
 * Contract R10 (audit 03/11/2026, Fase 5 T1): permessi solo tramite `can.*`.
 *
 *  1. Nessun confronto sul ruolo fuori da utils/permissions.js.
 *  2. Ogni funzione API che modifica dati e' classificata: o e' legata a
 *     una capability (GATED) o ha un motivo scritto per non esserlo
 *     (UNGATED). Una funzione nuova non classificata fa fallire il test.
 *  3. Ogni file che importa una funzione GATED usa la sua capability
 *     (`can.<nome>(`): l'azione si mostra solo a chi la puo' fare.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');
const apiRoot = path.join(srcRoot, 'api');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'mocks' ? [] : collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}
const rel = (p) => path.relative(srcRoot, p).split(path.sep).join('/');

// Funzione API -> capability che la governa (una o piu' alternative).
const GATED = {
  triggerBackup: ['manageBackups'],
  deleteAllStoragePreviews: ['manageObjectStorage'],
  cleanupOrphanStorage: ['manageObjectStorage'],
  deleteStorageObject: ['manageObjectStorage'],
  resolveUncertainCaricaRun: ['manageTaskRuntime'],
  createApiConnection: ['createApiConnection'],
  updateApiConnection: ['editApiConnection'],
  deleteApiConnection: ['editApiConnection'],
  createConnection: ['createConnection'],
  updateConnection: ['editConnection'],
  deleteConnection: ['editConnection'],
  createConnectionSet: ['createConnection'],
  updateConnectionSet: ['editConnection'],
  deleteConnectionSet: ['editConnection'],
  createFeatureConnection: ['createConnection'],
  updateFeatureConnection: ['editConnection'],
  deleteFeatureConnection: ['editConnection'],
  createDataSource: ['createDataSource'],
  updateDataSource: ['editDataSource'],
  deleteDataSource: ['editDataSource'],
  createEseguibile: ['createEseguibile'],
  updateEseguibile: ['manageEseguibile'],
  deleteEseguibile: ['manageEseguibile'],
  republishEseguibile: ['manageEseguibile'],
  activateEseguibileVersion: ['manageEseguibile'],
  uploadFile: ['uploadFile'],
  uploadMultiLayer: ['uploadFile'],
  updateFile: ['editFile', 'uploadFile'], // anche la scelta del layer dopo l'upload
  deleteFile: ['editFile'],
  createProject: ['createProject'],
  cloneProject: ['createProject'],
  updateProject: ['editProject'],
  deleteProject: ['deleteProject'],
  createSchedule: ['createSchedule'],
  updateSchedule: ['manageSchedule'],
  deleteSchedule: ['manageSchedule'],
  toggleSchedule: ['manageSchedule'],
  runScheduleNow: ['manageSchedule'],
  setShares: ['shareResources'],
  addShare: ['shareResources'],
  removeShare: ['shareResources'],
  updateTicket: ['manageTickets', 'reopenTicket'],
  deleteTicket: ['manageTickets'],
  deleteAttachment: ['removeTicketAttachment'],
  createUser: ['manageUsers'],
  updateUser: ['manageUsers'],
  deleteUser: ['manageUsers'],
  resetPassword: ['manageUsers'],
  createGroup: ['manageGroups'],
  deleteGroup: ['deleteGroup'],
};

// Funzioni che modificano dati senza una capability di ruolo, col motivo.
const UNGATED = {
  updateCanvas: 'scrittura del canvas: lo store e\' in sola lettura da can.editCanvas (useCanvasCore)',
  createCanvasVersion: 'versione del canvas: stessa sola lettura dello store',
  restoreCanvasVersion: 'ripristino del canvas: stessa sola lettura dello store',
  executeProjectQuery: 'esecuzione del progetto aperto: non distruttiva, regola del backend',
  executeEnrichmentAsync: 'esecuzione del progetto aperto: non distruttiva, regola del backend',
  previewProject: 'anteprima: sola lettura',
  pinRun: 'segnalibro su una run, non distruttivo',
  cancelTask: 'annulla un task avviato dall\'utente stesso',
  cleanupTaskCompletely: 'pulizia di un task dell\'utente stesso',
  createTicket: 'ogni utente della UI normale apre ticket (rotta /tickets)',
  addComment: 'ogni utente che vede il ticket commenta',
  uploadAttachment: 'ogni utente che vede il ticket allega',
  changeMyPassword: 'ogni utente cambia la propria password',
  createFeatureService: 'nessun chiamante (codice morto, T7)',
  writeFeatures: 'nessun chiamante (codice morto, T7)',
  executeEseguibile: 'ogni ruolo loggato esegue gli eseguibili che vede (can.runEseguibile vale per tutti)',
  runEseguibile: 'ogni ruolo loggato esegue gli eseguibili che vede (can.runEseguibile vale per tutti)',
  previewFileTemp: 'anteprima di un file non ancora caricato: sola lettura',
  previewFormula: 'anteprima di una formula: sola lettura',
};

// Componenti che chiamano una funzione GATED ma si aprono solo da un genitore
// che applica la capability: il test verifica che OGNI file che li importa
// sia in elenco e usi la capability. 'canvas' = l'azione dipende dalla sola
// lettura dello store (isReadOnly = !can.editCanvas, useCanvasCore).
const DELEGATED = {
  'components/api-conn/ApiConnectionWizard.jsx': ['pages/ApiConnectionManager.jsx'],
  'components/connections/ConnectionSetsPanel.jsx': ['pages/ConnectionManager.jsx'],
  'components/eseguibili/EseguibileVersionsModal.jsx': ['pages/EseguibiliList.jsx'],
  'components/eseguibili/PublishEseguibileModal.jsx': ['pages/EseguibiliList.jsx', 'pages/ProjectList.jsx'],
  'components/modals/ScheduleWizard.jsx': ['pages/ScheduleList.jsx'],
  'components/projects/NewProjectModal.jsx': ['pages/ProjectList.jsx'],
  'components/panels/FeatureServicePanel.jsx': ['pages/EstraiEditor.jsx'],
  'components/panels/ApiEnrichNode.jsx': 'canvas',
};

const MUTATING = /^(create|update|delete|remove|set|add|toggle|run|execute|restore|republish|activate|clone|cleanup|trigger|resolve|reset|upload|cancel|pin|change|write|preview)[A-Z]/;

function apiExports() {
  const names = new Set();
  for (const file of fs.readdirSync(apiRoot)) {
    if (!file.endsWith('.js')) continue;
    const content = fs.readFileSync(path.join(apiRoot, file), 'utf-8');
    for (const m of content.matchAll(/^export (?:async )?(?:function|const) ([A-Za-z0-9_]+)/gm)) names.add(m[1]);
  }
  return names;
}

// Nomi importati da un modulo api (../api, ../../api/x, ...).
function importedApiNames(content) {
  const names = [];
  for (const m of content.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]([^'"]*\/api(?:\/[A-Za-z0-9_]+)?|\.\/api)['"]/g)) {
    for (const part of m[1].split(',')) {
      const name = part.trim().split(/\s+as\s+/)[0].trim();
      if (name) names.push(name);
    }
  }
  return names;
}

describe('permessi solo tramite can.* (R10)', () => {
  it('nessun confronto sul ruolo fuori da utils/permissions.js', () => {
    // Unica eccezione: il ruolo di una relazione ArcGIS (origin/destination),
    // che non e' il ruolo dell'utente.
    const allowed = { 'utils/estraiJoins.js': /relation\.role === 'origin'/ };
    const violations = [];
    for (const file of collectSources(srcRoot)) {
      const name = rel(file);
      if (name === 'utils/permissions.js') continue;
      const lines = fs.readFileSync(file, 'utf-8').split('\n');
      lines.forEach((line, i) => {
        if (/^\s*(\*|\/\/)/.test(line)) return;
        const hit = /\brole\s*[!=]==?|[!=]==?\s*['"](admin|superadmin|viewer|esecutore)['"]|\b[a-zA-Z]*Role\s*[!=]==?/.test(line);
        if (hit && !(allowed[name] && allowed[name].test(line))) violations.push(`${name}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(violations).toEqual([]);
  });

  it('ogni funzione API che modifica dati e\' classificata', () => {
    const unclassified = [...apiExports()]
      .filter((name) => MUTATING.test(name))
      .filter((name) => !(name in GATED) && !(name in UNGATED));
    expect(unclassified).toEqual([]);
  });

  it('le capability della tabella esistono in can.*', () => {
    const missing = Object.values(GATED).flat().filter((cap) => typeof can[cap] !== 'function');
    expect(missing).toEqual([]);
  });

  it('chi importa una funzione GATED usa la sua capability (o la delega a un genitore che la usa)', () => {
    const files = collectSources(srcRoot).map((file) => ({ name: rel(file), content: fs.readFileSync(file, 'utf-8') }));
    const byName = Object.fromEntries(files.map((f) => [f.name, f.content]));
    const uses = (content, caps) => caps.some((cap) => content.includes(`can.${cap}(`));
    const importersOf = (target) => {
      const base = path.posix.basename(target).replace(/\.jsx?$/, '');
      const importRe = new RegExp(`from\\s+['"][^'"]*/${base}['"]`);
      return files.filter((f) => f.name !== target && importRe.test(f.content)).map((f) => f.name);
    };
    const violations = [];
    for (const { name, content } of files) {
      if (name.startsWith('api/')) continue;
      for (const fn of importedApiNames(content)) {
        const caps = GATED[fn];
        if (!caps || uses(content, caps)) continue;
        const delegated = DELEGATED[name];
        if (delegated === 'canvas') {
          if (!/isReadOnly/.test(content)) violations.push(`${name}: ${fn} nel canvas senza isReadOnly`);
          continue;
        }
        if (!delegated) {
          violations.push(`${name}: ${fn} senza can.${caps.join(' / can.')}(`);
          continue;
        }
        for (const importer of importersOf(name)) {
          if (!delegated.includes(importer)) violations.push(`${name}: aperto anche da ${importer}, non dichiarato`);
        }
        for (const parent of delegated) {
          if (!uses(byName[parent] || '', caps)) violations.push(`${parent}: apre ${name} (${fn}) senza can.${caps.join(' / can.')}(`);
        }
      }
    }
    expect([...new Set(violations)]).toEqual([]);
  });

  it('nessun window.prompt: le richieste di testo sono dialog del progetto (R10.5)', () => {
    const violations = collectSources(srcRoot)
      .filter((file) => /(^|[^.\w])(window\.)?prompt\s*\(/m.test(fs.readFileSync(file, 'utf-8')))
      .map(rel);
    expect(violations).toEqual([]);
  });
});

describe('tabella dei permessi: casi del backend', () => {
  const viewer = { role: 'viewer', user_id: 'v' };
  const esecutore = { role: 'esecutore', user_id: 'e' };
  const user = { role: 'user', user_id: 'u' };
  const admin = { role: 'admin', user_id: 'a' };
  const superadmin = { role: 'superadmin', user_id: 's' };

  it('schedule: azioni solo ad admin e proprietari', () => {
    expect(can.manageSchedule(viewer, {})).toBe(false);
    expect(can.manageSchedule(user, {})).toBe(false);
    expect(can.manageSchedule(user, { created_by: 'u' })).toBe(true);
    expect(can.manageSchedule(admin, {})).toBe(true);
  });

  it('cestino del progetto: mai a viewer ed esecutore; user solo sui propri', () => {
    expect(can.deleteProject(viewer, {})).toBe(false);
    expect(can.deleteProject(esecutore, {})).toBe(false);
    expect(can.deleteProject(user, { created_by: 'altro' })).toBe(false);
    expect(can.deleteProject(user, { created_by: 'u' })).toBe(true);
    expect(can.deleteProject(admin, { created_by: 'altro' })).toBe(true);
  });

  it('eseguibile: admin e flag can_edit del server', () => {
    expect(can.manageEseguibile(user, { can_edit: true })).toBe(false);
    expect(can.manageEseguibile(admin, { can_edit: false })).toBe(false);
    expect(can.manageEseguibile(admin, { can_edit: true })).toBe(true);
  });

  it('allegati: admin sempre, gli altri solo i propri', () => {
    expect(can.removeTicketAttachment(user, { uploaded_by: 'altro' })).toBe(false);
    expect(can.removeTicketAttachment(user, { uploaded_by: 'u' })).toBe(true);
    expect(can.removeTicketAttachment(viewer, { uploaded_by: 'v' })).toBe(true);
    expect(can.removeTicketAttachment(esecutore, { uploaded_by: 'e' })).toBe(false);
    expect(can.removeTicketAttachment(admin, { uploaded_by: 'altro' })).toBe(true);
  });

  it('gruppi: solo superadmin, mai il gruppo default', () => {
    expect(can.deleteGroup(admin, { group_id: 'g' })).toBe(false);
    expect(can.deleteGroup(superadmin, { group_id: 'default' })).toBe(false);
    expect(can.deleteGroup(superadmin, { group_id: 'g' })).toBe(true);
  });

  it('riapertura ticket: il creatore non admin su un ticket chiuso', () => {
    expect(can.reopenTicket(user, { created_by: 'u', status: 'closed' })).toBe(true);
    expect(can.reopenTicket(user, { created_by: 'u', status: 'open' })).toBe(false);
    expect(can.reopenTicket(admin, { created_by: 'a', status: 'closed' })).toBe(false);
  });
});
