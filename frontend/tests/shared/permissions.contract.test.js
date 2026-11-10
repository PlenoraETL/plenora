import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { can, CAPABILITY_BACKEND } from '../../src/utils/permissions';

/**
 * Contract R10 (audit 03/11/2026, Fase 5 T1): permessi solo tramite `can.*`.
 *
 *  1. Nessun confronto sul ruolo fuori da utils/permissions.js.
 *  2. Ogni funzione API che modifica dati e' classificata: o e' legata a
 *     una capability (GATED) o ha un motivo scritto per non esserlo
 *     (UNGATED). Una funzione nuova non classificata fa fallire il test.
 *  3. Ogni file che importa una funzione GATED usa la sua capability
 *     (`can.<nome>(`): l'azione si mostra solo a chi la puo' fare. Vale
 *     anche `RULES.<regola>(` (utils/nodeActions, 10/11/2026: i permessi
 *     condivisi tra le pagine e il pannello della home), se quella regola
 *     usa `can.<nome>(`.
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
  createEseguibileDashboard: ['manageEseguibileDashboard'],
  deleteEseguibileDashboard: ['manageEseguibileDashboard'],
  getEseguibileDashboardEditLink: ['manageEseguibileDashboard'],
  issueEseguibileDashboardPass: ['openEseguibileDashboard'],
  // Sessione per /grafana: prima dell'iframe o di «Modifica in Grafana».
  openGrafanaSession: ['openEseguibileDashboard', 'manageEseguibileDashboard'],
  uploadFile: ['uploadFile'],
  uploadMultiLayer: ['uploadFile'],
  updateFile: ['editFile', 'uploadFile'], // anche la scelta del layer dopo l'upload
  deleteFile: ['editFile'],
  createProject: ['createProject'],
  cloneProject: ['createProject'],
  updateProject: ['editProjectMeta'],
  deleteProject: ['deleteProject'],
  createSchedule: ['createSchedule'],
  updateSchedule: ['manageSchedule'],
  deleteSchedule: ['manageSchedule'],
  toggleSchedule: ['manageSchedule'],
  runScheduleNow: ['manageSchedule'],
  setShares: ['shareResources'],
  updateTicket: ['manageTickets', 'reopenTicket'],
  deleteTicket: ['manageTickets'],
  deleteAttachment: ['removeTicketAttachment'],
  createUser: ['manageUsers'],
  updateUser: ['manageUsers'],
  deleteUser: ['manageUsers'],
  deleteUserKeepingHistory: ['deleteUserKeepingHistory'],
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
  executeTestRun: 'esecuzione di prova: la offre solo il percorso unico (PipelineRunner) con can.testRunProject; il backend la vieta a viewer ed esecutore',
  previewProject: 'anteprima: sola lettura',
  previewProjectQuery: 'anteprima della query di Estrai sul canvas corrente: sola lettura, non salva (T6, R3.13)',
  previewIdentityTransform: "anteprima della trasformazione dell'«Utente che esegue» sul nome di prova: sola lettura, non salva",
  pinRun: 'segnalibro su una run, non distruttivo',
  cancelTask: 'annulla un task avviato dall\'utente stesso',
  cancelTaskOnPageExit: 'annulla, uscendo dalla pagina, un task avviato dall\'utente stesso',
  cleanupTaskCompletely: 'pulizia di un task dell\'utente stesso',
  createTicket: 'ogni utente della UI normale apre ticket (rotta /tickets)',
  addComment: 'ogni utente che vede il ticket commenta',
  uploadAttachment: 'ogni utente che vede il ticket allega',
  changeMyPassword: 'ogni utente cambia la propria password',
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
  'components/eseguibili/EseguibileVersionsModal.jsx': ['pages/EseguibiliList.jsx', 'pages/ProjectList.jsx', 'components/eseguibili/PublishedExecutablesNotice.jsx'],
  'components/eseguibili/PublishEseguibileModal.jsx': ['pages/EseguibiliList.jsx', 'pages/ProjectList.jsx'],
  'components/modals/ScheduleWizard.jsx': ['pages/ScheduleList.jsx'],
  'components/projects/NewProjectModal.jsx': ['pages/ProjectList.jsx'],
  'components/panels/FeatureServicePanel.jsx': ['pages/EstraiEditor.jsx'],
  'components/panels/ApiEnrichNode.jsx': 'canvas',
  // Gestori condivisi delle pagine Eseguibili e della home (pannello delle catene).
  'utils/resourceActions.js': ['pages/EseguibiliList.jsx', 'pages/ProjectList.jsx'],
  // Dashboard Grafana (11/11/2026): gestori comuni di pagina, pannello e finestra della dashboard.
  'utils/dashboardActions.js': ['pages/EseguibiliList.jsx', 'pages/ProjectList.jsx', 'components/eseguibili/DashboardLauncher.jsx'],
};

// RULES di utils/nodeActions: regola -> capability che usa (`can.<nome>(` nel corpo).
function ruleCapabilities() {
  const src = fs.readFileSync(path.join(srcRoot, 'utils/nodeActions.js'), 'utf-8');
  const body = src.split('export const RULES = Object.freeze({')[1].split('});')[0];
  const out = {};
  for (const m of body.matchAll(/^\s*([A-Za-z]+):\s*\(.*$/gm)) {
    out[m[1]] = [...m[0].matchAll(/can\.([A-Za-z]+)\(/g)].map((c) => c[1]);
  }
  return out;
}

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
    const rules = ruleCapabilities();
    const uses = (content, caps) => caps.some((cap) => content.includes(`can.${cap}(`)
      || Object.entries(rules).some(([rule, used]) => used.includes(cap) && content.includes(`RULES.${rule}(`)));
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

describe('ogni capability e\' la regola del backend del suo endpoint (CAPABILITY_BACKEND)', () => {
  const backendCore = path.resolve(import.meta.dirname, '../../../backend/app/core');
  const ROLE_LIST = ['esecutore', 'viewer', 'user', 'admin', 'superadmin'];
  // Ruoli ammessi da ogni dependency di ruolo (app/core/auth/dependencies.py).
  const ROLE_DEPS = {
    get_current_user: ROLE_LIST,
    require_not_esecutore: ['viewer', 'user', 'admin', 'superadmin'],
    _require_not_esecutore: ['viewer', 'user', 'admin', 'superadmin'],
    require_user_or_above: ['user', 'admin', 'superadmin'],
    require_admin: ['admin', 'superadmin'],
    require_superadmin: ['superadmin'],
  };
  const OWNER_DEPS = ['require_project_owner', 'require_file_owner', 'require_schedule_owner'];
  // Regola owns_or_admin (backend/app/core/auth/ownership.py).
  const ownerRule = (roleName, createdBy) => roleName === 'admin' || roleName === 'superadmin'
    || (!!createdBy && createdBy === 'me');

  // Le dependency di autenticazione di una funzione di router (Depends(...)).
  function authDepends(file, fn) {
    const source = fs.readFileSync(path.join(backendCore, file), 'utf-8');
    const start = source.indexOf(`\ndef ${fn}(`);
    if (start < 0) return null;
    // La firma finisce alla prima ")" seguita da ":" (o "-> Tipo:") a fine riga.
    const rest = source.slice(start);
    const end = rest.search(/\)\s*(->[^\n]*)?:[ \t]*\r?\n/);
    const signature = rest.slice(0, end < 0 ? 800 : end + 1);
    return [...signature.matchAll(/Depends\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)/g)]
      .map((m) => m[1])
      .filter((name) => name in ROLE_DEPS || OWNER_DEPS.includes(name));
  }

  it('ogni capability ha la sua regola del backend e viceversa', () => {
    expect(Object.keys(CAPABILITY_BACKEND).sort()).toEqual(Object.keys(can).sort());
  });

  it('le funzioni elencate esistono e hanno la dependency dichiarata', () => {
    const wrong = [];
    for (const [cap, spec] of Object.entries(CAPABILITY_BACKEND)) {
      for (const entry of spec.fns) {
        // `file::funzione`: funzione di un altro router (stessa regola).
        const [file, fn] = entry.includes('::') ? entry.split('::') : [spec.file, entry];
        const deps = authDepends(file, fn);
        if (deps === null) wrong.push(`${cap}: ${file}::${fn} non trovata`);
        else if (deps.length !== 1 || deps[0] !== spec.depends) wrong.push(`${cap}: ${file}::${fn} usa ${deps.join(', ') || 'nessuna'} (dichiarato ${spec.depends})`);
      }
    }
    expect(wrong).toEqual([]);
  });

  it('ruolo per ruolo il predicato da\' l\'esito della regola del backend', () => {
    const wrong = [];
    for (const [cap, spec] of Object.entries(CAPABILITY_BACKEND)) {
      if (spec.rule === 'service' || spec.stricter) continue;
      for (const roleName of ROLE_LIST) {
        const u = { role: roleName, user_id: 'me' };
        if (spec.rule === 'role') {
          const expected = ROLE_DEPS[spec.depends].includes(roleName);
          if (can[cap](u, {}) !== expected) wrong.push(`${cap}(${roleName}) = ${!expected}, backend ${expected}`);
        } else if (spec.rule === 'owner') {
          for (const createdBy of ['me', 'altro', undefined]) {
            const expected = ownerRule(roleName, createdBy);
            if (can[cap](u, { created_by: createdBy }) !== expected) {
              wrong.push(`${cap}(${roleName}, created_by=${createdBy}) != ${expected}`);
            }
          }
        }
        if (spec.serverFlag === true && can[cap](u, { can_edit: false })) wrong.push(`${cap}(${roleName}) ignora can_edit=false`);
        if (spec.serverFlag === 'exact') {
          for (const flag of [true, false]) {
            if (can[cap](u, { can_edit: flag, created_by: 'altro' }) !== flag) wrong.push(`${cap}(${roleName}) non segue can_edit=${flag}`);
          }
        }
      }
    }
    expect(wrong).toEqual([]);
  });

  it('le sole capability piu\' restrittive del backend hanno il motivo scritto', () => {
    const stricter = Object.entries(CAPABILITY_BACKEND).filter(([, s]) => s.stricter).map(([cap, s]) => [cap, s.stricter.length > 20]);
    expect(stricter).toEqual([['seeTechnicalLogs', true]]);
  });

  it('rinomina e tag del progetto: ogni user del gruppo; eliminazione: proprietario o admin', () => {
    const member = { role: 'user', user_id: 'u' };
    expect(can.editProjectMeta(member, { created_by: 'altro' })).toBe(true);
    expect(can.deleteProject(member, { created_by: 'altro' })).toBe(false);
    // Progetto senza created_by: solo admin (backend _check_ownership).
    expect(can.deleteProject(member, {})).toBe(false);
    expect(can.deleteProject({ role: 'admin', user_id: 'a' }, {})).toBe(true);
    // Schedule del viewer che l'ha creato: require_schedule_owner lo ammette.
    expect(can.manageSchedule({ role: 'viewer', user_id: 'v' }, { created_by: 'v' })).toBe(true);
  });
});

describe('RULES delle azioni della home (utils/nodeActions)', () => {
  it('ogni regola e\' una capability di can.* (o «always»)', () => {
    const rules = ruleCapabilities();
    expect(Object.keys(rules).length).toBeGreaterThan(5);
    for (const [rule, used] of Object.entries(rules)) {
      if (rule === 'always') { expect(used).toEqual([]); continue; }
      expect(used.length, rule).toBeGreaterThan(0);
      for (const cap of used) expect(typeof can[cap], `${rule} -> can.${cap}`).toBe('function');
    }
  });
});
