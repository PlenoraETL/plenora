/**
 * EsecutoreHome.jsx — Kiosk page per il ruolo "esecutore"
 *
 * Layout minimale, niente sidebar di navigazione: l'esecutore vede SOLO
 * la lista degli eseguibili visibili al proprio gruppo (owned + shared
 * cross-gruppo) e può lanciarli. Niente accesso al resto della UI.
 *
 * Privacy: a fine esecuzione la finestra chiama
 * `cleanupTaskCompletely(taskId)` per rimuovere ogni traccia
 * dell'esecuzione dal server (results.db, log entries, file su disco,
 * record del task): dopo il download, alla chiusura senza download e con
 * un esito fallito o annullato (Fase 5 T2, R9.3: useEseguibileRun).
 *
 * Route: /run
 * Accesso: solo utenti con role === 'esecutore'.
 *          Gli altri ruoli vedono questa stessa lista in /eseguibili.
 *
 * Top bar: logo + saluto + cambia password + logout. Niente altro.
 */
import React, { useState, useCallback, useMemo, useRef } from 'react';
import { listEseguibili, cleanupTaskCompletely } from '../api';
import { useAuth } from '../context/AuthContext';
import { useSignOut } from '../hooks/useSignOut';
import { useToast } from '../hooks/useToast';
import { useHexCanvas } from '../utils/hexCanvas';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import ChangePasswordModal from '../components/ChangePasswordModal';
import { useTheme } from '../hooks/useTheme';
import PlenoraLogo from '../components/common/PlenoraLogo';
import { useResource } from '../hooks/useResource';
import ListState from '../components/common/ListState';
import './EsecutoreHome.css';
import EmptyState from '../components/common/EmptyState';
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
    // a11y-1: la scheda e' un pulsante vero (tastiera, lettori di schermo).
    <button type="button" className="esh__card" onClick={() => onRun(item)}>
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
    </button>
  );
}
function EsecutoreHome() {
  const toast = useToast();
  const { user } = useAuth();

  // Fase 5 T2 (R4.8): un errore di caricamento e' un errore con «Riprova»,
  // mai "Nessuna azione disponibile".
  const resource = useResource(async () => {
    const data = await listEseguibili();
    return Array.isArray(data) ? data : [];
  });
  const items = useMemo(() => resource.data || [], [resource.data]);
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

  // Uscita volontaria: una sola via (hooks/useSignOut).
  const handleLogout = useSignOut();

  // Chiamato dalla finestra a fine esecuzione (scaricato o no, R9.3).
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
        <ListState
          resource={resource}
          loading={<div className="esh__loading">Caricamento azioni disponibili…</div>}
          errorTitle="Azioni non caricate"
          empty={(
            <EmptyState
              icon="📭"
              title="Nessuna azione disponibile"
              text="Al momento non ci sono azioni configurate per il tuo gruppo. Contatta un amministratore."
            />
          )}
        >
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
              <EmptyState icon="🔎" title="Nessun risultato" text={`Nessuna azione corrisponde a "${searchQuery}".`} />
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
        </ListState>
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
