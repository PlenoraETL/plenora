/**
 * AppSidebar — Sidebar di navigazione condivisa tra tutte le pagine
 *
 * Sostituisce le 8 sidebar copia-incolla che esistevano in:
 *   ProjectList, ConnectionManager, ApiConnectionManager, DataSourceManager,
 *   FileManager, ScheduleList, EseguibiliList, LogViewer, UserManager.
 *
 * Responsabilità:
 *  - Logo Plenora con dark/light awareness
 *  - Workspace section (Dashboard + scroll alle sezioni Estrai/Manipola/...)
 *  - Sistema section (link alle pagine /connections, /files, ecc.)
 *  - Footer utente con avatar + nome + ruolo (REALE) + logout
 *  - Pannello Admin (solo se can.manageUsers)
 *  - Highlight dell'item attivo via location.pathname
 *
 * Le voci del workspace (Estrai/Manipola/Arricchisci/Eseguibili) navigano
 * sempre alla dashboard "/" e poi scrollano alla sezione corrispondente
 * (via hash). ProjectList ascolta location.hash e fa lo scroll smooth.
 *
 * Nessuna prop (Fase 5 T7, L12): i badge sono gli stessi su ogni pagina (oggi
 * solo i ticket attivi, letti qui); prima comparivano solo sulla pagina che
 * passava il suo conteggio. Ogni voce è un <button> (a11y-1).
 *
 * Uso:
 *   la monta components/common/PageLayout (G1), nessun'altra pagina.
 */
import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useSignOut } from '../hooks/useSignOut';
import { can, role } from '../utils/permissions';
import { getTicketsCounts } from '../api/tickets';
import { useResource } from '../hooks/useResource';
import ChangePasswordModal from './ChangePasswordModal';
import { useTheme } from '../hooks/useTheme';
import PlenoraLogo from './common/PlenoraLogo';
import { enumLabel } from '../utils/format';
import './AppSidebar.css';
function Logo() {
  const { theme } = useTheme();
  const isDark = theme !== 'light';
  return (
    <PlenoraLogo
      width={130}
      height={36}
      plenFill={isDark ? '#FFFFFF' : '#0B1120'}
    />
  );
}
// Le sezioni della dashboard (workspace) sono identificate da hash:
//   /#estrai, /#manipola, /#arricchisci, /#eseguibili
// ProjectList ascolta location.hash e scrolla all'id `pl-section-<hash>`.

const WORKSPACE_ITEMS = [
  { key: 'dashboard',   label: 'Dashboard',   path: '/',              dot: '' },
  { key: 'estrai',      label: 'Estrai',      path: '/#estrai',       dot: 'estrai' },
  { key: 'manipola',    label: 'Manipola',    path: '/#manipola',     dot: 'manipola' },
  { key: 'arricchisci', label: 'Arricchisci', path: '/#arricchisci',  dot: 'arricchisci' },
  { key: 'eseguibili',  label: 'Eseguibili',  path: '/eseguibili',    dot: 'eseguibili' },
];

const SYSTEM_ITEMS = [
  { key: 'connections',  label: 'Connessioni',  path: '/connections' },
  { key: 'apis',         label: 'API Esterne',  path: '/external-connectors' },
  { key: 'datasources',  label: 'Data Source',  path: '/datasources' },
  { key: 'files',        label: 'File Manager', path: '/files' },
  { key: 'schedules',    label: 'Scheduler',    path: '/schedules' },
  { key: 'logs',         label: 'Log Viewer',   path: '/logs' },
  { key: 'tickets',      label: 'Ticket',       path: '/tickets',             countKey: 'tickets' },
];
export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const [showPwdModal, setShowPwdModal] = useState(false);

  // Badge ticket: count attivi visibili all'utente (self/group/global
  // a seconda del ruolo, enforcement backend). Aggiornato ogni 60s dall'hook
  // comune (useResource: un poll alla volta, fermo con la scheda nascosta).
  // Un errore nasconde il badge (dato secondario, nessun avviso all'utente).
  const countsResource = useResource(async () => {
    const data = await getTicketsCounts();
    return data.active_total ?? data.active_mine ?? 0;
  }, { key: ['tickets-counts', user?.user_id || null], enabled: Boolean(user) && !role.isEsecutore(user), refreshInterval: 60_000 });
  const ticketsActiveCount = countsResource.status === 'error' ? null : (countsResource.data ?? null);

  const counts = { tickets: ticketsActiveCount || undefined };

  // Per le voci workspace l'highlight si basa sul fatto che siamo sulla
  // dashboard e l'hash corrisponde. Per il sistema basta confrontare il
  // pathname.
  const isWorkspaceActive = (item) => {
    if (item.key === 'dashboard') {
      return location.pathname === '/' && (location.hash === '' || location.hash === '#');
    }
    if (!item.path.startsWith('/#')) return location.pathname === item.path;
    return location.pathname === '/' && location.hash === `#${item.key}`;
  };
  const isSystemActive = (item) => location.pathname === item.path;

  // Uscita volontaria: una sola via (hooks/useSignOut).
  const handleLogout = useSignOut();

  return (
    <nav className="app-sidebar">

      {/* Logo */}
      <div className="app-sidebar__logo">
        <Logo />
        <div className="app-sidebar__sub">GeoData Platform</div>
      </div>

      {/* Voci di navigazione */}
      <div className="app-sidebar__nav">

        <div className="app-sidebar__nav-label">Workspace</div>
        {WORKSPACE_ITEMS.map(item => {
          const active = isWorkspaceActive(item);
          return (
            <button
              type="button"
              key={item.key}
              className={`app-sidebar__item${active ? ' app-sidebar__item--active' : ''}`}
              aria-current={active ? 'page' : undefined}
              onClick={() => navigate(item.path)}
            >
              <span className={`app-sidebar__dot${item.dot ? ` app-sidebar__dot--${item.dot}` : ''}`} />
              {item.label}
            </button>
          );
        })}

        <div className="app-sidebar__nav-label" style={{ marginTop: 8 }}>Sistema</div>
        {SYSTEM_ITEMS.map(item => {
          const active = isSystemActive(item);
          const count = item.countKey ? counts[item.countKey] : null;
          return (
            <button
              type="button"
              key={item.key}
              className={`app-sidebar__item${active ? ' app-sidebar__item--active' : ''}`}
              aria-current={active ? 'page' : undefined}
              onClick={() => navigate(item.path)}
            >
              <span className="app-sidebar__dot" />
              {item.label}
              {count != null && <span className="app-sidebar__count">{count}</span>}
            </button>
          );
        })}
      </div>

      {/* Footer utente */}
      <div className="app-sidebar__footer">
        {can.manageUsers(user) && (
          <button
            type="button"
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/users')}
            title="Gestione utenti e gruppi"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">⚙️</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Admin Panel</div>
              <div className="app-sidebar__user-role">utenti &amp; gruppi</div>
            </div>
          </button>
        )}
        {role.isSuperadmin(user) && (
          <button
            type="button"
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/task-runtime')}
            title="Code RabbitMQ, worker e task attivi"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">MQ</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Code e Worker</div>
              <div className="app-sidebar__user-role">RabbitMQ · superadmin</div>
            </div>
          </button>
        )}
        {role.isSuperadmin(user) && (
          <button
            type="button"
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/object-storage')}
            title="Explorer object storage runtime"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">S3</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Object Storage</div>
              <div className="app-sidebar__user-role">file runtime · superadmin</div>
            </div>
          </button>
        )}
        {role.isSuperadmin(user) && (
          <button
            type="button"
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/system-status')}
            title="Dashboard stato sistema"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">📊</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Stato Sistema</div>
              <div className="app-sidebar__user-role">overview · superadmin</div>
            </div>
          </button>
        )}
        {role.isSuperadmin(user) && (
          <button
            type="button"
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/backups')}
            title="Backup database su S3"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">💾</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Backup DB</div>
              <div className="app-sidebar__user-role">S3 · solo superadmin</div>
            </div>
          </button>
        )}
        {role.isSuperadmin(user) && (
          <button
            type="button"
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/runtime-config')}
            title="Configurazione runtime"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">⚙️</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Config Runtime</div>
              <div className="app-sidebar__user-role">read-only · superadmin</div>
            </div>
          </button>
        )}
        <div className="app-sidebar__user-row">
          <div className="app-sidebar__avatar">
            {(user?.username || 'U').slice(0, 2).toUpperCase()}
          </div>
          <div className="app-sidebar__user-info">
            <div className="app-sidebar__user-name">{user?.username || 'Utente'}</div>
            <div className="app-sidebar__user-role">{enumLabel('role', user?.role)}</div>
          </div>
          <button
            type="button"
            className="app-sidebar__icon-btn"
            onClick={() => setShowPwdModal(true)}
            title="Cambia password"
          >
            🔑
          </button>
          <button
            type="button"
            className="app-sidebar__logout-btn"
            onClick={handleLogout}
            title="Logout"
          >
            ⎋
          </button>
        </div>
      </div>

      {showPwdModal && <ChangePasswordModal onClose={() => setShowPwdModal(false)} />}
    </nav>
  );
}
