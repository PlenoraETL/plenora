/**
 * navItems — UNICA tabella delle destinazioni di navigazione (UI-4, U23).
 *
 * La leggono la barra laterale (components/AppSidebar) e la palette Ctrl+K
 * (components/common/CommandPalette): stesso nome, stessa icona e stessa
 * descrizione per la stessa pagina. Prima la barra diceva «Scheduler» e
 * «File Manager», la palette «Schedule» e «File».
 *
 *  - section: 'progetti' (in alto nella barra), 'sistema', 'admin' (in fondo);
 *  - access: chi la vede (nessuno = ogni utente collegato; 'manageUsers' =
 *    amministratori; 'superadmin'); la rotta resta comunque guardata in App.jsx;
 *  - dot: colore del punto nella barra (tipi di progetto);
 *  - countKey: badge con un conteggio (ticket attivi);
 *  - sub: seconda riga delle voci di amministrazione.
 */
import { ICON } from './icons';
import { PROJECT_TYPES } from './projectTypes';
import { can, role } from './permissions';

const typeItem = (type) => ({
  key: type, section: 'progetti', label: PROJECT_TYPES[type].label, icon: PROJECT_TYPES[type].icon,
  hint: `Progetti ${PROJECT_TYPES[type].label}`, path: `/#${type}`, dot: type,
});

export const NAV_ITEMS = Object.freeze([
  { key: 'dashboard', section: 'progetti', label: 'Dashboard', icon: ICON.dashboard, hint: 'Tutti i progetti, per tipo', path: '/' },
  typeItem('estrai'),
  typeItem('manipola'),
  typeItem('arricchisci'),
  { key: 'eseguibili', section: 'progetti', label: 'Eseguibili', icon: ICON.executable, hint: 'Progetti pubblicati per chi li esegue', path: '/eseguibili', dot: 'eseguibili' },

  { key: 'connections', section: 'sistema', label: 'Connessioni', icon: ICON.connection, hint: 'Database e servizi ArcGIS', path: '/connections' },
  { key: 'apis', section: 'sistema', label: 'API esterne', icon: ICON.api, hint: 'Connettori verso API e servizi REST', path: '/external-connectors' },
  { key: 'datasources', section: 'sistema', label: 'Data Source', icon: ICON.datasource, hint: 'SharePoint · FTP · S3', path: '/datasources' },
  { key: 'files', section: 'sistema', label: 'File', icon: ICON.file, hint: 'File caricati', path: '/files' },
  { key: 'schedules', section: 'sistema', label: 'Pianificazioni', icon: ICON.schedule, hint: 'Esecuzioni automatiche programmate', path: '/schedules' },
  { key: 'logs', section: 'sistema', label: 'Log', icon: ICON.logs, hint: 'Esecuzioni e log tecnici', path: '/logs' },
  { key: 'tickets', section: 'sistema', label: 'Ticket', icon: ICON.ticket, hint: 'Segnalazioni e supporto', path: '/tickets', countKey: 'tickets' },

  { key: 'users', section: 'admin', access: 'manageUsers', label: 'Utenti e gruppi', icon: ICON.users, hint: 'Gestione di utenti, gruppi e permessi', sub: 'amministrazione', path: '/admin/users' },
  { key: 'task-runtime', section: 'admin', access: 'superadmin', label: 'Code e worker', icon: ICON.task_runtime, hint: 'Code RabbitMQ, worker e task attivi', sub: 'RabbitMQ · superadmin', path: '/admin/task-runtime' },
  { key: 'object-storage', section: 'admin', access: 'superadmin', label: 'Archivio S3', icon: ICON.object_storage, hint: 'File di lavoro nell\'archivio S3', sub: 'file di lavoro · superadmin', path: '/admin/object-storage' },
  { key: 'system-status', section: 'admin', access: 'superadmin', label: 'Stato del sistema', icon: ICON.system_status, hint: 'Servizi, code e spazio', sub: 'panoramica · superadmin', path: '/admin/system-status' },
  { key: 'backups', section: 'admin', access: 'superadmin', label: 'Backup del database', icon: ICON.backup, hint: 'Backup del database su S3', sub: 'S3 · superadmin', path: '/admin/backups' },
  { key: 'runtime-config', section: 'admin', access: 'superadmin', label: 'Configurazione', icon: ICON.runtime_config, hint: 'Configurazione del server (sola lettura)', sub: 'sola lettura · superadmin', path: '/admin/runtime-config' },
]);

const ACCESS = {
  manageUsers: (user) => can.manageUsers(user),
  superadmin: (user) => role.isSuperadmin(user),
};

/** Voci che l'utente vede (stessa regola per barra e palette). */
export const visibleNavItems = (user, section) => NAV_ITEMS
  .filter(item => !section || item.section === section)
  .filter(item => !item.access || ACCESS[item.access](user));
