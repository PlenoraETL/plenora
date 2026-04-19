/**
 * AppHeader — Header unificato per tutte le pagine
 *
 * Props:
 *  - icon:     string  — emoji icona (es. "🔮")
 *  - title:    string  — titolo pagina
 *  - subtitle: string  — sottotitolo (opzionale)
 *  - backTo:   string | -1  — se presente, mostra bottone "← Indietro"
 *                             usa "/" per home, -1 per navigate(-1)
 *  - children: ReactNode — bottoni azione custom (destra, prima di logout)
 *
 * Uso:
 *   <AppHeader icon="📋" title="Log di Sistema" subtitle="Monitoraggio pipeline" backTo="/">
 *     <button type="button" className="app-header__btn" onClick={handleExport}>Export</button>
 *   </AppHeader>
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { can } from '../utils/permissions';
import ChangePasswordModal from './ChangePasswordModal';
import './AppHeader.css';

export default function AppHeader({ icon, title, subtitle, backTo, children }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [showPwdModal, setShowPwdModal] = useState(false);

  const handleBack = () => {
    if (backTo === -1 || backTo === '-1') {
      navigate(-1);
    } else {
      navigate(backTo);
    }
  };

  const handleAdminPanel = () => navigate('/admin/users');

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <header className="app-header">
      <div className="app-header__container">

        {/* SINISTRA: back + titolo */}
        <div className="app-header__left">
          {backTo !== undefined && (
            <button type="button" className="app-header__back-btn" onClick={handleBack}>
              ← Indietro
            </button>
          )}
          <div>
            <h1 className="app-header__title">
              {icon && <span className="app-header__icon">{icon}</span>}
              {title}
            </h1>
            {subtitle && (
              <p className="app-header__subtitle">{subtitle}</p>
            )}
          </div>
        </div>

        {/* DESTRA: azioni custom + utente + logout */}
        <div className="app-header__right">
          {children && (
            <div className="app-header__actions">
              {children}
            </div>
          )}
          <div className="app-header__user-section">
            {can.manageUsers(user) && (
              <button type="button" className="app-header__btn" onClick={handleAdminPanel} title="Gestione utenti">
                ⚙️ Admin
              </button>
            )}
            <span className="app-header__username">👤 {user?.username}</span>
            <button
              type="button"
              className="app-header__btn"
              onClick={() => setShowPwdModal(true)}
              title="Cambia password"
            >
              🔑
            </button>
            <button type="button" className="app-header__btn app-header__btn--logout" onClick={handleLogout}>
              ⎋ Esci
            </button>
          </div>
        </div>

      </div>
      {showPwdModal && <ChangePasswordModal onClose={() => setShowPwdModal(false)} />}
    </header>
  );
}
