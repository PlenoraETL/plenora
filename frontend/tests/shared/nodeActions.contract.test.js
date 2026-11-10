/**
 * Home: azioni e «Visibile a» per ogni tipo di nodo (10/11/2026).
 *
 *  1. Enumerazione della tabella NODE_ACTIONS: ogni tipo di nodo ha
 *     esattamente le azioni attese, ogni azione una regola di RULES.
 *  2. Stesse funzioni e stessi permessi delle pagine di origine: le pagine
 *     (EseguibiliList, ConnectionManager, ProjectList) leggono i permessi da
 *     RULES e usano gli stessi gestori e finestre (lettura statica).
 *  3. Permessi per ruolo: «Condividi» solo al superadmin; gestione degli
 *     Eseguibili ad admin e superadmin; il server (`can_edit: false`) blocca.
 *  4. panelActions: gestore + permesso + stato.
 *  5. visibilityOf: progetto (tutto il gruppo), proprietario + gruppi,
 *     tutti i gruppi («*»), condivisioni non dichiarate, dati non caricati.
 *  6. Il salvataggio delle condivisioni invalida la home (dashboardGraph).
 *  7. Richiesta «Modifica» di una connessione alla pagina che si apre.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import {
  NODE_ACTIONS, RULES, accessIndex, connectionShareType, panelActions, visibilityOf,
} from '../../src/utils/nodeActions';
import { invalidationsForWrite } from '../../src/utils/sharedInvalidations';
import { connectionEditKey, requestOnOpen, takeOnOpen, takeRunOnOpen, requestRunOnOpen } from '../../src/utils/pendingRun';

const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');

const superadmin = { role: 'superadmin', group_id: 'grp-1', username: 's' };
const admin = { role: 'admin', group_id: 'grp-1', username: 'a' };
const user = { role: 'user', group_id: 'grp-1', username: 'u' };

describe('tabella delle azioni per tipo di nodo', () => {
  it('ogni tipo di nodo ha le azioni attese', () => {
    const ids = Object.fromEntries(Object.entries(NODE_ACTIONS).map(([k, list]) => [k, list.map((a) => a.id)]));
    expect(ids).toEqual({
      project: ['open', 'run', 'history', 'schedules', 'rename', 'clone', 'publish', 'remove'],
      eseguibile: ['openSource', 'run', 'publishVersion', 'share', 'rename', 'versions', 'remove'],
      connection: ['edit', 'openPage', 'test', 'share'],
      file: ['openPage'],
      datasource: ['openPage'],
    });
  });

  it('ogni azione ha una regola di RULES, un posto e un\'etichetta', () => {
    for (const [kind, list] of Object.entries(NODE_ACTIONS)) {
      for (const a of list) {
        expect(typeof RULES[a.rule], `${kind}.${a.id}`).toBe('function');
        expect(['primary', 'button', 'menu']).toContain(a.place);
        expect(a.label).toBeTruthy();
      }
      // Un solo pulsante principale per tipo.
      expect(list.filter((a) => a.place === 'primary').length, kind).toBeLessThanOrEqual(1);
    }
  });

  it('le regole delle azioni sono quelle delle pagine di origine', () => {
    const rule = (kind, id) => NODE_ACTIONS[kind].find((a) => a.id === id).rule;
    expect(rule('eseguibile', 'share')).toBe('shareEseguibile');
    for (const id of ['rename', 'versions', 'remove', 'publishVersion']) expect(rule('eseguibile', id)).toBe('manageEseguibile');
    expect(rule('connection', 'share')).toBe('shareConnection');
    expect(rule('connection', 'edit')).toBe('editConnection');
    expect(rule('project', 'rename')).toBe('editProjectMeta');
    expect(rule('project', 'clone')).toBe('createProject');
    expect(rule('project', 'publish')).toBe('createEseguibile');
    expect(rule('project', 'remove')).toBe('deleteProject');
  });
});

describe('stesse funzioni e stessi permessi delle pagine (lettura statica)', () => {
  const esl = read('pages/EseguibiliList.jsx');
  const cm = read('pages/ConnectionManager.jsx');
  const pl = read('pages/ProjectList.jsx');

  it('pagina Eseguibili: permessi da RULES, gestori e finestre comuni', () => {
    expect(esl).toContain('RULES.shareEseguibile(user, item)');
    expect(esl).toContain('RULES.manageEseguibile(user, item)');
    expect(esl).not.toMatch(/can\.(shareResources|manageEseguibile)\(/);
    for (const used of ['confirmDeleteEseguibile(', 'saveEseguibileName(', '<ShareDialog', '<EseguibileVersionsModal']) expect(esl).toContain(used);
    expect(esl).not.toContain('AllowedGroupsSelector');
  });

  it('pagina Connessioni: permessi da RULES, test e finestra comuni', () => {
    expect(cm).toContain('RULES.editConnection(user, c)');
    expect(cm).toContain('RULES.shareConnection(user)');
    expect(cm).not.toMatch(/can\.(shareResources|editConnection)\(/);
    for (const used of ['testConnectionOf(', '<ShareDialog', 'connectionShareType(', "takeOnOpen('editConnection'"]) expect(cm).toContain(used);
    expect(cm).not.toContain('AllowedGroupsSelector');
  });

  it('home (pannello): gli stessi gestori e le stesse finestre', () => {
    for (const used of [
      'confirmDeleteEseguibile(', 'renameEseguibile(', 'testConnectionOf(', '<ShareDialog', '<EseguibileVersionsModal',
      'connectionShareType(', "requestOnOpen('editConnection'", 'handleCloneProject(p)', 'handleDeleteProject(p.id, p.name)',
      'setPublishProject(p)', 'handleRenameProject(p, name)',
    ]) expect(pl).toContain(used);
    // I permessi della home vengono dalla tabella (nessun can.* nella pagina).
    expect(pl).not.toMatch(/\bcan\.[a-zA-Z]+\(/);
    expect(read('components/projects/ChainsView.jsx')).toContain('panelActions(node,');
  });

  it('la finestra «Condividi» usa la tabella condivisibile del nodo', () => {
    expect(connectionShareType('arcgis')).toBe('arcgis_connections');
    expect(connectionShareType('database')).toBe('connections');
    expect(read('components/shared/ShareDialog.jsx')).toContain('<AllowedGroupsSelector');
  });
});

describe('permessi per ruolo', () => {
  const item = { id: 'x', can_edit: true };
  it.each([
    ['superadmin', superadmin, { shareEseguibile: true, shareConnection: true, manageEseguibile: true, editConnection: true }],
    ['admin', admin, { shareEseguibile: false, shareConnection: false, manageEseguibile: true, editConnection: true }],
    ['user', user, { shareEseguibile: false, shareConnection: false, manageEseguibile: false, editConnection: false }],
  ])('%s', (_, who, expected) => {
    for (const [rule, value] of Object.entries(expected)) expect(RULES[rule](who, item), rule).toBe(value);
    expect(RULES.runEseguibile(who)).toBe(true);
  });

  it('una risorsa bloccata dal server non si condivide ne\' si gestisce', () => {
    const locked = { id: 'x', can_edit: false };
    expect(RULES.shareEseguibile(superadmin, locked)).toBe(false);
    expect(RULES.manageEseguibile(admin, locked)).toBe(false);
  });
});

describe('panelActions', () => {
  const eseguibile = { kind: 'eseguibile', type: 'eseguibile', id: 'x' };
  const all = Object.fromEntries(NODE_ACTIONS.eseguibile.map((a) => [a.id, () => a.id]));

  it('superadmin: tutte, «Pubblica nuova versione» solo se il progetto e\' cambiato', () => {
    const ids = (item) => panelActions(eseguibile, { user: superadmin, item, handlers: all }).map((a) => a.id);
    expect(ids({ id: 'x', can_edit: true })).toEqual(['openSource', 'run', 'share', 'rename', 'versions', 'remove']);
    expect(ids({ id: 'x', can_edit: true, source_changed: true })).toContain('publishVersion');
  });

  it('admin: niente «Condividi»; user: solo apri ed esegui', () => {
    const item = { id: 'x', can_edit: true };
    expect(panelActions(eseguibile, { user: admin, item, handlers: all }).map((a) => a.id))
      .toEqual(['openSource', 'run', 'rename', 'versions', 'remove']);
    expect(panelActions(eseguibile, { user, item, handlers: all }).map((a) => a.id)).toEqual(['openSource', 'run']);
  });

  it('senza gestore l\'azione non c\'e\'; il gestore e\' quello dato', () => {
    const list = panelActions(eseguibile, { user: superadmin, item: { id: 'x' }, handlers: { run: all.run, openSource: null } });
    expect(list.map((a) => a.id)).toEqual(['run']);
    expect(list[0].onSelect()).toBe('run');
  });

  it('connessione: Modifica solo a chi la modifica, Condividi solo al superadmin', () => {
    const conn = { kind: 'origin', type: 'connection', id: 'c1', connectionKind: 'db' };
    const handlers = { edit: () => {}, openPage: () => {}, test: () => {}, share: () => {} };
    const ids = (who) => panelActions(conn, { user: who, item: { id: 'c1', can_edit: true }, handlers }).map((a) => a.id);
    expect(ids(superadmin)).toEqual(['edit', 'openPage', 'test', 'share']);
    expect(ids(admin)).toEqual(['edit', 'openPage', 'test']);
    expect(ids(user)).toEqual(['openPage', 'test']);
  });
});

describe('«Visibile a»', () => {
  const groups = { 'grp-1': 'Uffici', 'grp-2': 'Patrimonio', 'grp-3': 'Ospiti' };
  const access = accessIndex({
    access: [
      { resource_type: 'eseguibili', id: 'x1', group_id: 'grp-1', shared_with: ['grp-3', 'grp-2'] },
      { resource_type: 'eseguibili', id: 'x2', group_id: 'grp-1', shared_with: ['*'] },
      { resource_type: 'eseguibili', id: 'x3', group_id: 'grp-1', shared_with: [] },
      { resource_type: 'connections', id: 'c1', group_id: 'grp-2', shared_with: null },
      { resource_type: 'arcgis_connections', id: 'c1', group_id: 'grp-1', shared_with: null },
      { resource_type: 'file_sources', id: 'f1', group_id: 'grp-1', shared_with: null },
    ],
  });
  const ctx = { access, groups, user: admin };
  const ese = (id) => ({ kind: 'eseguibile', type: 'eseguibile', id });

  it('progetto: tutto il gruppo', () => {
    expect(visibilityOf({ kind: 'project', type: 'estrai', id: 'p', raw: { group_id: 'grp-1' } }, ctx))
      .toEqual({ lines: ['Tutto il gruppo Uffici'], note: 'I progetti non si condividono con altri gruppi.' });
  });

  it('proprietario + gruppi della condivisione; «*» = tutti i gruppi; nessuna condivisione', () => {
    expect(visibilityOf(ese('x1'), ctx).lines).toEqual(['Gruppo Uffici (proprietario)', 'Gruppo Ospiti', 'Gruppo Patrimonio']);
    expect(visibilityOf(ese('x2'), ctx).lines).toEqual(['Gruppo Uffici (proprietario)', 'Tutti i gruppi']);
    expect(visibilityOf(ese('x3'), ctx)).toEqual({ lines: ['Gruppo Uffici (proprietario)'], note: 'Non condiviso con altri gruppi.' });
  });

  it('condivisioni non dichiarate (non superadmin): proprietario e, se di un altro gruppo, il tuo', () => {
    const c1 = visibilityOf({ kind: 'origin', type: 'connection', id: 'c1', connectionKind: 'db' }, ctx);
    expect(c1.lines).toEqual(['Gruppo Patrimonio (proprietario)', 'Il tuo gruppo, per condivisione']);
    expect(c1.note).toMatch(/superadmin/);
    // Stesso id ma ArcGIS: un'altra tabella.
    expect(visibilityOf({ kind: 'origin', type: 'connection', id: 'c1', connectionKind: 'arcgis' }, ctx).lines)
      .toEqual(['Gruppo Uffici (proprietario)']);
    expect(visibilityOf({ kind: 'origin', type: 'file', id: 'f1' }, ctx).lines).toEqual(['Gruppo Uffici (proprietario)']);
  });

  it('dati non caricati o risorsa non visibile; nome del gruppo assente = id', () => {
    expect(visibilityOf(ese('nope'), { ...ctx, unavailable: new Set(['access']) }).lines).toEqual(['Non caricato']);
    expect(visibilityOf(ese('nope'), ctx).lines).toEqual(['Non disponibile']);
    expect(visibilityOf(ese('x2'), { ...ctx, groups: {} }).lines[0]).toBe('Gruppo grp-1 (proprietario)');
  });
});

describe('invalidazione e richieste alla pagina che si apre', () => {
  it.each(['eseguibili/x', 'connections/c1', 'arcgis_connections/c1'])('PUT /shares/%s invalida la home', (target) => {
    expect(invalidationsForWrite('put', `/shares/${target}`)).toContain('dashboardGraph');
  });

  it('«Modifica» di una connessione: per categoria e id, una volta', () => {
    requestOnOpen('editConnection', connectionEditKey('database', 'c1'));
    expect(takeOnOpen('editConnection', 'arcgis:c1')).toBe(false);
    expect(takeOnOpen('editConnection', 'db:c1')).toBe(true);
    expect(takeOnOpen('editConnection', 'db:c1')).toBe(false);
    // Le richieste di tipo diverso non si toccano.
    requestRunOnOpen('p1');
    requestOnOpen('editConnection', 'db:c2');
    expect(takeRunOnOpen('p1')).toBe(true);
    expect(takeOnOpen('editConnection', 'db:c2')).toBe(true);
    expect(() => requestOnOpen('altro', 'x')).toThrow();
  });
});
