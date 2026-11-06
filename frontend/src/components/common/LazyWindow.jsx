/**
 * LazyWindow — finestre caricate al bisogno dentro le pagine (Versioni,
 * Anteprima, Esecuzione, scelta della connessione) (conferma finale G1).
 *
 * Prima negli editor una sola ModalErrorBoundary avvolgeva tutte le finestre
 * lazy: se dopo un deploy il file di «Versioni» non c'era piu', l'errore si
 * portava via anche ExecutionModal; la fallback offriva solo «Chiudi», che
 * non chiudeva (lo stato `show*` restava vero e lazy rilanciava l'errore); e
 * non c'era «Ricarica». Ora, per costruzione:
 *  - `lazyWindow(loader, nome)` e' l'UNICO modo di caricare al bisogno una
 *    finestra (test tests/shared/uiConfermaFinale.contract): ogni finestra ha
 *    la SUA boundary e il SUO Suspense, quindi una finestra rotta non tocca
 *    le altre;
 *  - chiusa (`isOpen` falso) non si monta e non scarica niente; aperta e
 *    rotta mostra «Chiudi», che chiama il SUO `onClose` (lo stato `show*` del
 *    chiamante torna falso; riaprendo si riprova da capo), e «Ricarica la
 *    pagina», che passa dalla domanda delle modifiche non salvate
 *    (utils/chunkReload.reloadAfterLeaveQuestion) prima di ricaricare.
 */
import React, { Suspense, useEffect } from 'react';
import { reportError } from '../../utils/errorReporting';
import { reloadAfterLeaveQuestion } from '../../utils/chunkReload';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import './ErrorBoundary.css';

function WindowFallback({ name, onClose }) {
  const confirm = useConfirmOrDeny();
  return (
    <div className="error-boundary error-boundary--modal" role="alert" data-lazy-window-error={name}>
      <div className="error-boundary__icon" aria-hidden="true">⚠️</div>
      <p className="error-boundary__message">
        La finestra «{name}» non si è aperta. Se è appena uscita una versione nuova di Plenora,
        ricarica la pagina.
      </p>
      <div className="error-boundary__actions">
        <button type="button" className="error-boundary__btn error-boundary__btn--primary"
          onClick={() => { reloadAfterLeaveQuestion(confirm); }}>
          Ricarica la pagina
        </button>
        <button type="button" className="error-boundary__btn error-boundary__btn--secondary" onClick={onClose}>
          Chiudi
        </button>
      </div>
    </div>
  );
}

class WindowBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error: error ?? new Error('errore imprevisto') };
  }

  componentDidCatch(error, info) {
    this.props.onFailure?.();
    reportError(error, info, `finestra:${this.props.name}`);
  }

  render() {
    if (this.state.error) return <WindowFallback name={this.props.name} onClose={this.props.onClose} />;
    return this.props.children;
  }
}

/** La finestra aperta: boundary e Suspense suoi; chiusa non si monta. */
function LazyWindowFrame({ windowState, windowName, ...props }) {
  // `prefetch` (H7): il file si scarica gia' all'apertura della pagina, cosi'
  // un deploy successivo non lo toglie proprio quando serve (Esecuzione).
  useEffect(() => { windowState.prefetch?.(); }, [windowState]);
  if (!props.isOpen) {
    // React.lazy ricorda per sempre un caricamento fallito: chiusa dopo un
    // errore, si prepara un caricatore nuovo, cosi' riaprendo si riprova.
    if (windowState.failed) windowState.reset();
    return null;
  }
  const { Lazy } = windowState;
  // `onClose` della finestra: anche la fallback chiude davvero.
  const close = () => props.onClose?.();
  return (
    <WindowBoundary name={windowName} onClose={close} onFailure={windowState.fail}>
      <Suspense fallback={null}>
        <Lazy {...props} />
      </Suspense>
    </WindowBoundary>
  );
}

/**
 * Una finestra caricata al bisogno, con la sua boundary.
 * @param {() => Promise<{default: React.ComponentType}>} loader
 * @param {string} name nome della finestra per l'utente («Versioni», ...)
 * @param {{prefetch?: boolean}} [options] `prefetch`: scarica il file alla
 *   prima apparizione della pagina, non all'apertura della finestra (per la
 *   finestra che serve proprio quando si esegue: Esecuzione, H7)
 */
export function lazyWindow(loader, name, { prefetch = false } = {}) {
  let warm = null;
  const windowState = {
    Lazy: React.lazy(loader),
    failed: false,
    fail() { windowState.failed = true; },
    reset() { windowState.Lazy = React.lazy(loader); windowState.failed = false; },
    // Una volta: un errore qui non conta (lo mostra l'apertura, come sempre).
    prefetch: prefetch ? () => { warm = warm || Promise.resolve().then(loader).catch(() => {}); } : null,
  };
  const render = (props) => <LazyWindowFrame {...props} windowState={windowState} windowName={name} />;
  render.displayName = `LazyWindow(${name})`;
  return render;
}

