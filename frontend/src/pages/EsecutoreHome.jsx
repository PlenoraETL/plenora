/**
 * EsecutoreHome.jsx — Kiosk page per il ruolo "esecutore"
 *
 * Layout minimale, niente sidebar di navigazione: l'esecutore vede SOLO
 * la lista degli eseguibili visibili al proprio gruppo (owned + shared
 * cross-gruppo) e può lanciarli. Niente accesso al resto della UI.
 *
 * Privacy: dopo il download del risultato la wizard chiama
 * `cleanupTaskCompletely(taskId)` per rimuovere ogni traccia
 * dell'esecuzione dal server (results.db, log entries, file su disco,
 * record Celery).
 *
 * Route: /run
 * Accesso: solo utenti con role === 'esecutore'.
 *          Gli altri ruoli vedono questa stessa lista in /eseguibili.
 *
 * Top bar: logo + saluto + cambia password + logout. Niente altro.
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { listEseguibili, cleanupTaskCompletely } from '../api';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../hooks/useToast';
import { useHexCanvas } from '../utils/hexCanvas';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import ChangePasswordModal from '../components/ChangePasswordModal';
import { useTheme } from '../hooks/useTheme';
import PlenoraLogo from '../components/common/PlenoraLogo';
import { toastApiError } from '../utils/toastApiError';
import './EsecutoreHome.css';
function Logo() {
  const { theme } = useTheme();
  const isDark = theme !== 'light';
  return (
    <PlenoraLogo
      width={120}
      height={32}
      plenFill={isDark ? '#FFFFFF' : '#0B1120'}
    />
  );
}
function EseguibileCard({ item, onRun }) {
  return (
    <div className="esh__card" onClick={() => onRun(item)}>
      <div className="esh__card-icon">{item.icon || '▶'}</div>
      <div className="esh__card-body">
        <div className="esh__card-title">{item.name}</div>
        {item.description && (
          <div className="esh__card-description">{item.description}</div>
        )}
        <div className="esh__card-meta">
          <span className="esh__card-meta-item">
            📋 {item.expected_columns_count ?? 0} colonne attese
          </span>
          {item.default_output_format && (
            <span className="esh__card-meta-item">
              📄 Output: {item.default_output_format}
            </span>
          )}
        </div>
      </div>
      <div className="esh__card-cta">
        Esegui →
      </div>
    </div>
  );
}
function EsecutoreHome() {
  const navigate = useNavigate();
  const toast = useToast();
  const { user, logout } = useAuth();

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedItem, setSelectedItem] = useState(null);
  const [showPwdModal, setShowPwdModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

  const filteredItems = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return items;
    return items.filter(it =>
      (it.name || '').toLowerCase().includes(q) ||
      (it.description || '').toLowerCase().includes(q)
    );
  }, [items, searchQuery]);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listEseguibili();
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      toastApiError(toast, e, 'Errore caricamento eseguibili');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { loadItems(); }, [loadItems]);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  // Hook che la wizard chiama dopo un download riuscito.
  // Per l'esecutore: cleanup completo del task (privacy GDPR).
  // Pre-fix (P0 contracts-flows): un cleanup parziale (HTTP 207 dal BE)
  // o un network error veniva silenziato — il claim GDPR "nessuna
  // traccia dell'esecuzione" risultava violato senza segnalazione UI.
  // Post-fix: legge `partialCleanup` dal return strutturato e mostra
  // toast all'utente in caso di problemi, per trasparenza.
  const handleAfterDownload = useCallback(async (taskId) => {
    try {
      const result = await cleanupTaskCompletely(taskId);
      if (result?.partialCleanup) {
        const detail = result.networkError
          ? 'errore di rete durante la richiesta di cleanup'
          : `step falliti: ${(result.failedSteps || []).join(', ') || 'sconosciuti'}`;
        toast.warning(
          `Cleanup post-download parziale (${detail}). ` +
          `Alcuni dati dell'esecuzione potrebbero richiedere intervento ` +
          `dell'amministratore per la rimozione completa.`,
          8000,
        );
      }
    } catch (e) {
      // Catch ridondante: cleanupTaskCompletely non solleva mai
      // (cattura internamente), ma manteniamo per sicurezza.
      console.warn('Cleanup post-download fallito:', e);
    }
  }, [toast]);

  return (
    <div className="esh">
      <canvas ref={canvasRef} className="esh__hex-bg" />
<header className="esh__topbar">
        <div className="esh__topbar-left">
          <Logo />
        </div>
        <div className="esh__topbar-right">
          <span className="esh__user-greeting">
            Ciao, <strong>{user?.username || 'utente'}</strong>
          </span>
          <button
            type="button"
            className="esh__icon-btn"
            title="Cambia password"
            onClick={() => setShowPwdModal(true)}
          >
            🔑
          </button>
          <button
            type="button"
            className="esh__icon-btn esh__icon-btn--logout"
            title="Esci"
            onClick={handleLogout}
          >
            ⎋
          </button>
        </div>
      </header>
<div className="esh__hero">
        <h1 className="esh__hero-title">Le tue azioni disponibili</h1>
        <p className="esh__hero-subtitle">
          Seleziona un'azione per caricare i tuoi dati e ottenere il risultato.
          I file caricati ed elaborati non vengono conservati sulla piattaforma.
        </p>
      </div>
<main className="esh__main">
        {loading ? (
          <div className="esh__loading">Caricamento azioni disponibili…</div>
        ) : items.length === 0 ? (
          <div className="esh__empty">
            <div className="esh__empty-icon">📭</div>
            <h3>Nessuna azione disponibile</h3>
            <p>
              Al momento non ci sono azioni configurate per il tuo gruppo.
              Contatta un amministratore.
            </p>
          </div>
        ) : (
          <>
            <div className="esh__search">
              <span className="esh__search-icon" aria-hidden="true">🔍</span>
              <input
                type="text"
                className="esh__search-input"
                placeholder="Cerca per nome o descrizione…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {searchQuery && (
                <button
                  type="button"
                  className="esh__search-clear"
                  title="Cancella ricerca"
                  onClick={() => setSearchQuery('')}
                >
                  ✕
                </button>
              )}
            </div>
            {filteredItems.length === 0 ? (
              <div className="esh__empty">
                <div className="esh__empty-icon">🔎</div>
                <h3>Nessun risultato</h3>
                <p>Nessuna azione corrisponde a "{searchQuery}".</p>
              </div>
            ) : (
              <div className="esh__grid">
                {filteredItems.map(item => (
                  <EseguibileCard
                    key={item.id}
                    item={item}
                    onRun={(it) => setSelectedItem(it)}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </main>
{selectedItem && (
        <EseguibileExecuteWizard
          eseguibileId={selectedItem.id}
          onClose={() => setSelectedItem(null)}
          onAfterDownload={handleAfterDownload}
        />
      )}
{showPwdModal && (
        <ChangePasswordModal onClose={() => setShowPwdModal(false)} />
      )}
    </div>
  );
}

export default EsecutoreHome;
