/**
 * DashboardLauncher — «📊 Apri dashboard» nella finestra dei risultati di
 * un Eseguibile e la finestra grande con la dashboard Grafana (11/11/2026).
 *
 * UNO per tutti i percorsi: modals/ExecutionModal (finestra con il
 * manifest) ed EseguibileExecuteWizard (finestra legacy) lo montano con il
 * bersaglio calcolato da useEseguibileRun (`run.dashboard`, regola unica
 * utils/eseguibileDashboard.dashboardTarget). Senza bersaglio (nessuna
 * dashboard, output a file, risultato non piu' disponibile) non c'e' nulla
 * e una dashboard aperta si chiude; chiudere la finestra dei risultati la
 * smonta (rinnovi fermi: hooks/useDashboardPass).
 *
 * iframe: `src` relativo sotto /grafana/ (stesso origine, CSP
 * `frame-src 'self'`), mai `srcDoc`, `referrerPolicy="no-referrer"`,
 * titolo accessibile. `sandbox`: Grafana ha bisogno di script e del proprio
 * origine (cookie della sessione, localStorage), quindi
 * `allow-scripts allow-same-origin` e la sandbox NON e' un confine di
 * sicurezza (stesso origine): resta per non lasciare all'iframe la
 * navigazione della pagina di Plenora (niente allow-top-navigation). Il
 * confine vero e' sul server (forward_auth, cookie Path=/grafana,
 * lasciapassare per esecuzione).
 */
import React, { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useOptionalToast } from '../../context/ToastContext';
import { useDashboardPass } from '../../hooks/useDashboardPass';
import { can } from '../../utils/permissions';
import { openDashboardEditor } from '../../utils/dashboardActions';
import { formatTime } from '../../utils/format';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import DashboardWarnings from './DashboardWarnings';
import './DashboardLauncher.css';

/** Permessi dell'iframe (vedi sopra): niente navigazione della pagina che lo contiene. */
export const DASHBOARD_IFRAME_SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads';

// Utente della sessione o null (finestra aperta anche fuori da AuthProvider: test, anteprime).
function useSessionUser() {
  try {
    return useAuth()?.user ?? null;
  } catch {
    return null;
  }
}

const targetKey = (target) => (target ? `${target.eseguibileId}|${target.taskId}` : null);

function DashboardViewer({ target, user, onClose }) {
  const toast = useOptionalToast();
  const pass = useDashboardPass({ user, target, open: true });
  const [opening, setOpening] = useState(false);
  const canEdit = can.manageEseguibileDashboard(user, target.item);
  const title = `Dashboard — ${target.item?.name || 'Eseguibile'}`;

  const edit = async () => {
    setOpening(true);
    try {
      await openDashboardEditor({ toast, item: target.item, taskId: target.taskId });
    } finally {
      setOpening(false);
    }
  };

  return (
    <ModalWrapper
      isOpen
      onClose={onClose}
      title={title}
      icon="📊"
      size="full"
      className="dashboard-viewer"
      closeOnOverlay={false}
      footer={(
        <>
          {canEdit && (
            <ModalButton variant="secondary" onClick={edit} loading={opening}>📊 Modifica in Grafana</ModalButton>
          )}
          <ModalButton variant="secondary" onClick={onClose}>Chiudi</ModalButton>
        </>
      )}
    >
      <div className="dashboard-viewer__body">
        {pass.status === 'loading' && <p className="dashboard-viewer__status" role="status">Apertura della dashboard…</p>}
        {pass.status === 'error' && (
          <div className="dashboard-viewer__status" role="alert">
            <p>{pass.error}</p>
            <ModalButton variant="secondary" onClick={pass.retry}>Riprova</ModalButton>
          </div>
        )}
        {pass.status === 'expired' && (
          <p className="dashboard-viewer__status" role="status">
            I dati di questa esecuzione non sono più disponibili per la dashboard: esegui di nuovo per vederne di nuovi.
          </p>
        )}
        {pass.status === 'ready' && pass.iframePath && (
          <>
            <iframe
              key={pass.iframePath}
              className="dashboard-viewer__frame"
              src={pass.iframePath}
              title={`Dashboard Grafana del risultato di ${target.item?.name || 'questo Eseguibile'}`}
              referrerPolicy="no-referrer"
              sandbox={DASHBOARD_IFRAME_SANDBOX}
            />
            {pass.dataExpiresAt && (
              <p className="dashboard-viewer__hint">
                Dati di questa esecuzione disponibili fino alle {formatTime(pass.dataExpiresAt)}, finché questa finestra dei risultati resta aperta.
              </p>
            )}
          </>
        )}
      </div>
    </ModalWrapper>
  );
}

/**
 * `target`: bersaglio di dashboardTarget o null. `buttonClassName`: lo
 * stile del pulsante della finestra che lo ospita.
 */
export default function DashboardLauncher({ target, buttonClassName = 'dashboard-launcher__btn' }) {
  const user = useSessionUser();
  const [openKey, setOpenKey] = useState(null);
  const key = targetKey(target);
  if (!target) return null;
  const open = openKey !== null && openKey === key;
  return (
    <div className="dashboard-launcher">
      <button type="button" className={buttonClassName} onClick={() => setOpenKey(key)}>
        📊 Apri dashboard
      </button>
      <DashboardWarnings warnings={target.warnings} />
      {open && <DashboardViewer target={target} user={user} onClose={() => setOpenKey(null)} />}
    </div>
  );
}
