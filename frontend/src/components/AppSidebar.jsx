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
 * Props:
 *  - counts?: { projects, connections, files, schedules, apiConnections, eseguibili, ... }
 *             — opzionale, mostra badge numerici accanto alle voci
 *
 * Uso:
 *   <div className="page">
 *     <AppSidebar />
 *     <main className="page__main">...</main>
 *   </div>
 */
import React, { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { can, role } from '../utils/permissions';
import { getTicketsCounts } from '../api/tickets';
import ChangePasswordModal from './ChangePasswordModal';
import './AppSidebar.css';

// ── Logo Plenora (dark/light aware) ─────────────────────────────────────
function Logo() {
  const [isDark, setIsDark] = useState(
    () => (localStorage.getItem('plenora-theme') || 'dark') !== 'light'
  );
  useEffect(() => {
    const obs = new MutationObserver(() =>
      setIsDark(document.documentElement.getAttribute('data-theme') !== 'light')
    );
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);
  return (
    <svg width="130" height="36" viewBox="0 0 350 90" xmlns="http://www.w3.org/2000/svg">
      <polygon points="16,58 52,40 88,58 52,76" fill="rgba(245,166,35,0.10)"  stroke="#F5A623" strokeWidth="1.4" strokeLinejoin="round"/>
      <polygon points="16,44 52,26 88,44 52,62" fill="rgba(155,109,255,0.10)" stroke="#9B6DFF" strokeWidth="1.4" strokeLinejoin="round"/>
      <polygon points="16,30 52,12 88,30 52,48" fill="rgba(52,193,122,0.12)"  stroke="#34C17A" strokeWidth="2"   strokeLinejoin="round"/>
      <text x="108" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" fill={isDark ? '#FFFFFF' : '#0B1120'}>Plen</text>
      <text x="220" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" fill="#4B8BFF">ora</text>
    </svg>
  );
}

// ── Definizioni voci nav ────────────────────────────────────────────────
// Le sezioni della dashboard (workspace) sono identificate da hash:
//   /#estrai, /#manipola, /#arricchisci, /#eseguibili
// ProjectList ascolta location.hash e scrolla all'id `pl-section-<hash>`.

const WORKSPACE_ITEMS = [
  { key: 'dashboard',   label: 'Dashboard',   path: '/',              dot: '' },
  { key: 'estrai',      label: 'Estrai',      path: '/#estrai',       dot: 'estrai' },
  { key: 'manipola',    label: 'Manipola',    path: '/#manipola',     dot: 'manipola' },
  { key: 'arricchisci', label: 'Arricchisci', path: '/#arricchisci',  dot: 'arricchisci' },
  { key: 'eseguibili',  label: 'Eseguibili',  path: '/#eseguibili',   dot: 'eseguibili' },
];

const SYSTEM_ITEMS = [
  { key: 'connections',  label: 'Connessioni',  path: '/connections',         countKey: 'connections' },
  { key: 'apis',         label: 'API Esterne',  path: '/external-connectors', countKey: 'apiConnections' },
  { key: 'datasources',  label: 'Data Source',  path: '/datasources',         countKey: 'datasources' },
  { key: 'files',        label: 'File Manager', path: '/files',               countKey: 'files' },
  { key: 'schedules',    label: 'Scheduler',    path: '/schedules',           countKey: 'schedules' },
  { key: 'logs',         label: 'Log Viewer',   path: '/logs' },
  { key: 'tickets',      label: 'Ticket',       path: '/tickets',             countKey: 'tickets' },
];

// ── Componente principale ───────────────────────────────────────────────
export default function AppSidebar({ counts = {} }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const [showPwdModal, setShowPwdModal] = useState(false);

  // Badge ticket: count attivi visibili all'utente (self/group/global
  // a seconda del ruolo, enforcement backend). Refresh ogni 60s —
  // tradeoff: abbastanza veloce perche' l'utente veda cambiamenti
  // senza reload manuale, abbastanza lento da non saturare di
  // richieste un admin aperto su 10 tab.
  const [ticketsActiveCount, setTicketsActiveCount] = useState(null);
  useEffect(() => {
    if (!user || role.isEsecutore(user)) return undefined;
    let cancelled = false;
    // Logga l'errore al massimo una volta per sessione del componente
    // — il polling 60s renderebbe inutile spammare console.warn ad ogni
    // tick se /tickets/counts e' down. Pieno fail-silent rendeva
    // invisibile la regressione (badge sparito = "feature disattivata?").
    let warnedThisSession = false;
    const load = async () => {
      try {
        const data = await getTicketsCounts();
        if (cancelled) return;
        const n = (data.active_total ?? data.active_mine ?? 0);
        setTicketsActiveCount(n);
        warnedThisSession = false;  // recuperata
      } catch (e) {
        if (!cancelled) setTicketsActiveCount(null);
        if (!warnedThisSession) {
          // eslint-disable-next-line no-console
          // console.warn diagnostico volutamente non instradato a toast: il
          // badge tickets è secondario, l'endpoint può essere temporaneamente
          // assente (lab/canary) e non vogliamo notifica utente per ogni load.
          console.warn(
            '[AppSidebar] /tickets/counts non raggiungibile, badge nascosto:',
            e?.response?.status || e?.message,
          );
          warnedThisSession = true;
        }
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [user]);

  // Merge del count ticket dentro `counts` senza modificare la prop
  // passata dall'esterno (alcuni chiamanti potrebbero gia' popolare
  // counts.tickets — in quel caso il prop esterno vince).
  const mergedCounts = {
    ...counts,
    tickets: counts.tickets != null
      ? counts.tickets
      : (ticketsActiveCount || undefined),
  };

  // Per le voci workspace l'highlight si basa sul fatto che siamo sulla
  // dashboard e l'hash corrisponde. Per il sistema basta confrontare il
  // pathname.
  const isWorkspaceActive = (item) => {
    if (item.key === 'dashboard') {
      return location.pathname === '/' && (location.hash === '' || location.hash === '#');
    }
    return location.pathname === '/' && location.hash === `#${item.key}`;
  };
  const isSystemActive = (item) => location.pathname === item.path;

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

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
            <div
              key={item.key}
              className={`app-sidebar__item${active ? ' app-sidebar__item--active' : ''}`}
              onClick={() => navigate(item.path)}
            >
              <span className={`app-sidebar__dot${item.dot ? ` app-sidebar__dot--${item.dot}` : ''}`} />
              {item.label}
              {mergedCounts.workspace?.[item.key] != null && (
                <span className="app-sidebar__count">{mergedCounts.workspace[item.key]}</span>
              )}
            </div>
          );
        })}

        <div className="app-sidebar__nav-label" style={{ marginTop: 8 }}>Sistema</div>
        {SYSTEM_ITEMS.map(item => {
          const active = isSystemActive(item);
          const count = item.countKey ? mergedCounts[item.countKey] : null;
          return (
            <div
              key={item.key}
              className={`app-sidebar__item${active ? ' app-sidebar__item--active' : ''}`}
              onClick={() => navigate(item.path)}
            >
              <span className="app-sidebar__dot" />
              {item.label}
              {count != null && <span className="app-sidebar__count">{count}</span>}
            </div>
          );
        })}
      </div>

      {/* Footer utente */}
      <div className="app-sidebar__footer">
        {can.manageUsers(user) && (
          <div
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/users')}
            title="Gestione utenti e gruppi"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">⚙️</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Admin Panel</div>
              <div className="app-sidebar__user-role">utenti &amp; gruppi</div>
            </div>
          </div>
        )}
        {role.isSuperadmin(user) && (
          <div
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/system-status')}
            title="Dashboard stato sistema"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">📊</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Stato Sistema</div>
              <div className="app-sidebar__user-role">overview · superadmin</div>
            </div>
          </div>
        )}
        {role.isSuperadmin(user) && (
          <div
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            onClick={() => navigate('/admin/backups')}
            title="Backup database su S3"
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin">💾</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">Backup DB</div>
              <div className="app-sidebar__user-role">S3 · solo superadmin</div>
            </div>
          </div>
        )}
        <div className="app-sidebar__user-row">
          <div className="app-sidebar__avatar">
            {(user?.username || 'U').slice(0, 2).toUpperCase()}
          </div>
          <div className="app-sidebar__user-info">
            <div className="app-sidebar__user-name">{user?.username || 'Utente'}</div>
            <div className="app-sidebar__user-role">{user?.role || '—'}</div>
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
