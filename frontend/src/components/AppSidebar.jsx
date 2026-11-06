/**
 * AppSidebar — Sidebar di navigazione condivisa tra tutte le pagine
 *
 * Sostituisce le 8 sidebar copia-incolla che esistevano in:
 *   ProjectList, ConnectionManager, ApiConnectionManager, DataSourceManager,
 *   FileManager, ScheduleList, EseguibiliList, LogViewer, UserManager.
 *
 * Responsabilità:
 *  - Logo Plenora con dark/light awareness
 *  - sezione Progetti (dashboard + scroll alle sezioni Estrai/Manipola/...)
 *  - Sistema section (link alle pagine /connections, /files, ecc.)
 *  - Footer utente con avatar + nome + ruolo (REALE) + logout
 *  - Pannello Admin (solo se can.manageUsers)
 *  - Highlight dell'item attivo via location.pathname
 *
 * Le voci dei tipi di progetto (Estrai/Manipola/Arricchisci) navigano
 * sempre alla dashboard "/" e poi scrollano alla sezione corrispondente
 * (via hash). ProjectList ascolta location.hash e fa lo scroll smooth.
 *
 * Voci, nomi, icone e descrizioni dalla tabella unica utils/navItems, la
 * stessa della palette Ctrl+K (UI-4, U23). La barra si chiude a sole icone
 * (UI-4, U26: a 1366 px lascia spazio al contenuto); la scelta resta in
 * questo browser (hooks/useStoredState).
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
import { role } from '../utils/permissions';
import { getTicketsCounts } from '../api/tickets';
import { useResource } from '../hooks/useResource';
import { useStoredState, LAYOUT_KEYS } from '../hooks/useStoredState';
import ChangePasswordModal from './ChangePasswordModal';
import { useTheme } from '../hooks/useTheme';
import PlenoraLogo from './common/PlenoraLogo';
import { enumLabel } from '../utils/format';
import { visibleNavItems } from '../utils/navItems';
import { ICON } from '../utils/icons';
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
// Le sezioni della dashboard sono identificate da hash:
//   /#estrai, /#manipola, /#arricchisci
// ProjectList ascolta location.hash e scrolla all'id `pl-section-<hash>`.

const SECTION_LABELS = { progetti: 'Progetti', sistema: 'Sistema' };
const asBoolean = (v) => v === true;

export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const [showPwdModal, setShowPwdModal] = useState(false);
  const [collapsed, setCollapsed] = useStoredState(LAYOUT_KEYS.appSidebarCollapsed, false, asBoolean);

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

  // Voce attiva: per le sezioni della dashboard (/#tipo) contano pagina e
  // hash; la dashboard senza hash e' «Dashboard»; per le altre il
  // percorso.
  const isActive = (item) => {
    if (item.path === '/') return location.pathname === '/' && (location.hash === '' || location.hash === '#');
    if (item.path.startsWith('/#')) return location.pathname === '/' && location.hash === item.path.slice(1);
    return location.pathname === item.path;
  };

  // Uscita volontaria: una sola via (hooks/useSignOut).
  const handleLogout = useSignOut();

  const navButton = (item) => {
    const active = isActive(item);
    const count = item.countKey ? counts[item.countKey] : null;
    return (
      <button
        type="button"
        key={item.key}
        className={`app-sidebar__item${active ? ' app-sidebar__item--active' : ''}`}
        aria-current={active ? 'page' : undefined}
        title={collapsed ? `${item.label} — ${item.hint}` : item.hint}
        onClick={() => navigate(item.path)}
      >
        {collapsed
          ? <span className="app-sidebar__item-icon" aria-hidden="true">{item.icon}</span>
          : <span className={`app-sidebar__dot${item.dot ? ` app-sidebar__dot--${item.dot}` : ''}`} />}
        <span className="app-sidebar__item-label">{item.label}</span>
        {count != null && <span className="app-sidebar__count">{count}</span>}
      </button>
    );
  };

  return (
    <nav className={`app-sidebar${collapsed ? ' app-sidebar--collapsed' : ''}`} aria-label="Navigazione">

      {/* Logo */}
      <div className="app-sidebar__logo">
        {!collapsed && <Logo />}
        <button
          type="button"
          className="app-sidebar__collapse"
          onClick={() => setCollapsed(c => !c)}
          aria-expanded={!collapsed}
          title={collapsed ? 'Apri la barra laterale' : 'Chiudi la barra laterale (resta solo con le icone)'}
          aria-label={collapsed ? 'Apri la barra laterale' : 'Chiudi la barra laterale'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>

      {/* Voci di navigazione */}
      <div className="app-sidebar__nav">
        {Object.entries(SECTION_LABELS).map(([section, label], i) => (
          <React.Fragment key={section}>
            <div className="app-sidebar__nav-label" style={i ? { marginTop: 8 } : undefined}>{label}</div>
            {visibleNavItems(user, section).map(navButton)}
          </React.Fragment>
        ))}
      </div>

      {/* Footer utente */}
      <div className="app-sidebar__footer">
        {visibleNavItems(user, 'admin').map(item => (
          <button
            type="button"
            key={item.key}
            className="app-sidebar__user-row app-sidebar__user-row--admin"
            aria-current={isActive(item) ? 'page' : undefined}
            onClick={() => navigate(item.path)}
            title={item.hint}
          >
            <div className="app-sidebar__avatar app-sidebar__avatar--admin" aria-hidden="true">{item.icon}</div>
            <div className="app-sidebar__user-info">
              <div className="app-sidebar__user-name">{item.label}</div>
              <div className="app-sidebar__user-role">{item.sub}</div>
            </div>
          </button>
        ))}
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
            {ICON.password}
          </button>
          <button
            type="button"
            className="app-sidebar__logout-btn"
            onClick={handleLogout}
            title="Esci"
            aria-label="Esci"
          >
            {ICON.logout}
          </button>
        </div>
      </div>

      {showPwdModal && <ChangePasswordModal onClose={() => setShowPwdModal(false)} />}
    </nav>
  );
}
