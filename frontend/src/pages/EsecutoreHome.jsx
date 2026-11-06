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
 * un esito fallito o annullato (Fase 5 T2, R9.3: useEseguibileRun), alla
 * scadenza della copia scaricata e chiudendo la scheda o il browser
 * (`releaseTaskOnPageExit`, fetch keepalive; UI conferma B-K4). Una
 * pulizia che la pagina non ha visto confermare (keepalive non partita,
 * pagina messa nella cache del browser e mai tornata) riparte alla
 * prossima apertura di /run (`flushParkedExitCleanups`, conferma finale
 * G4/G10); l'ordine annulla-poi-pulisci e' del server (F8/G3). Cio' che non
 * arriva mai al server lo toglie la scadenza lato server degli output degli
 * Eseguibili (2 ore).
 *
 * Route: /run
 * Accesso: solo utenti con role === 'esecutore'.
 *          Gli altri ruoli vedono questa stessa lista in /eseguibili.
 *
 * Top bar: logo + saluto + cambia password + logout. Niente altro.
 */
import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { cleanupTaskCompletely, releaseTaskOnPageExit } from '../api';
import { flushParkedExitCleanups, parkTaskCleanup } from '../api/tasks';
import { unparkExitCleanup } from '../utils/exitCleanups';
import { useAuth } from '../context/AuthContext';
import { useSignOut } from '../hooks/useSignOut';
import { useToast } from '../hooks/useToast';
import { useHexCanvas } from '../utils/hexCanvas';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import { runInputSummary, usesRunForm } from '../components/eseguibili/runForm';
import ChangePasswordModal from '../components/ChangePasswordModal';
import { useTheme } from '../hooks/useTheme';
import PlenoraLogo from '../components/common/PlenoraLogo';
import { useSharedResource } from '../hooks/useSharedResource';
import { eseguibiliOf } from '../utils/sharedSelectors';
import ListState from '../components/common/ListState';
import './EsecutoreHome.css';
import EmptyState from '../components/common/EmptyState';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';

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
          {/* UI-4 (U14): con il modulo dinamico non ci sono colonne da
              preparare: si compila un modulo (mai «0 colonne attese»). La
              scelta e' quella della finestra (runForm, dal manifest). */}
          <span className="esh__card-meta-item">
            {usesRunForm(item) ? '📝' : '📋'} {runInputSummary(item)}
          </span>
          {item.default_output_format && (
            <span className="esh__card-meta-item">
              📄 Risultato: {item.default_output_format}
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
  // UI-1: risorsa condivisa `eseguibili` (utils/sharedResources).
  const resource = useSharedResource('eseguibili', { select: eseguibiliOf });
  const items = useMemo(() => resource.data || [], [resource.data]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [showPwdModal, setShowPwdModal] = useState(false);
  const search = useSearch();

  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

  const filteredItems = useMemo(() => search.filter(items, it => [it.name, it.description]), [items, search.filter]);

  // Uscita volontaria: una sola via (hooks/useSignOut).
  const handleLogout = useSignOut();

  // Chiamato dalla finestra a fine esecuzione (scaricato o no, R9.3).
  // Per l'esecutore: cleanup completo del task (privacy GDPR).
  // Pre-fix (P0 contracts-flows): un cleanup parziale (HTTP 207 dal BE)
  // o un network error veniva silenziato — il claim GDPR "nessuna
  // traccia dell'esecuzione" risultava violato senza segnalazione UI.
  // Post-fix: legge `partialCleanup` dal return strutturato e mostra
  // toast all'utente in caso di problemi, per trasparenza.
  // UI conferma B-K4: chiudendo la scheda (`pageExit`) la pulizia parte con
  // fetch keepalive (una richiesta: l'ordine annulla-poi-pulisci e' del
  // server, F8/G3): non c'e' una pagina a cui mostrare l'esito. Pagina messa
  // nella cache del browser (`persisted`, G4): solo annotata; se torna
  // (`pageReturn`) l'annotazione si toglie e il risultato resta.
  const handleAfterDownload = useCallback(async (taskId, { pageExit = false, persisted = false, pageReturn = false } = {}) => {
    if (pageReturn) return unparkExitCleanup(taskId);
    if (pageExit) {
      if (persisted) parkTaskCleanup(taskId);
      else releaseTaskOnPageExit(taskId);
      return undefined;
    }
    try {
      const result = await cleanupTaskCompletely(taskId);
      if (result?.partialCleanup) {
        const detail = result.networkError
          ? 'errore di rete durante la pulizia'
          : `passaggi non riusciti: ${(result.failedSteps || []).join(', ') || 'sconosciuti'}`;
        toast.warning(
          `Pulizia dopo lo scaricamento incompleta (${detail}). ` +
          `Alcuni dati dell'esecuzione potrebbero richiedere intervento ` +
          `dell'amministratore per la rimozione completa.`,
          8000,
        );
      } else if (result?.deferred) {
        toast.info("L'esecuzione si sta fermando: il server ne toglierà ogni traccia appena è ferma.", 6000);
      }
    } catch (e) {
      // Catch ridondante: cleanupTaskCompletely non solleva mai
      // (cattura internamente), ma manteniamo per sicurezza.
      console.warn('Cleanup post-download fallito:', e);
    }
    return undefined;
  }, [toast]);

  // Pulizie rimaste annotate (pagina chiusa senza conferma del server o
  // messa nella cache del browser e mai tornata): ripartono ora (G4, G10).
  useEffect(() => { flushParkedExitCleanups().catch(() => {}); }, []);

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
            <SearchField
              search={search}
              label="Cerca per nome o descrizione"
              className="esh__search"
              shown={filteredItems.length}
              total={items.length}
            />
            {filteredItems.length === 0 ? (
              <SearchEmpty search={search} shown={0} />
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
