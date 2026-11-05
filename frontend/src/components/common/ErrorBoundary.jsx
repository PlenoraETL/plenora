/**
 * ErrorBoundary.jsx — Error Boundary per area (R5.7).
 *
 * Un solo meccanismo (`AreaErrorBoundary`), quattro aree:
 *
 *   <AppErrorBoundary>    — fuori dal Router: ultima istanza (provider,
 *     CommandPalette, ...). Pagina di errore con Ricarica / Torna alla home.
 *   <RouteErrorBoundary>  — dentro il Router, intorno alle pagine: un crash in
 *     una pagina sostituisce solo quella pagina e si azzera cambiando rotta.
 *   <CanvasErrorBoundary> — il canvas degli editor.
 *   <ModalErrorBoundary>  — un pannello o una modale.
 *
 * Ogni boundary invia l'errore a Sentry se e' configurato (utils/errorReporting:
 * in produzione React non rilancia gli errori catturati) e mostra un testo
 * italiano: il messaggio tecnico dell'eccezione solo in sviluppo.
 */
import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { reportError } from '../../utils/errorReporting';
import { FRONTEND_ENV } from '../../config/runtimeEnv';
import './ErrorBoundary.css';

const SHOW_TECHNICAL_DETAIL = FRONTEND_ENV.isDev;

class AreaErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error: error ?? new Error('errore imprevisto') };
  }

  componentDidCatch(error, info) {
    reportError(error, info, this.props.area);
  }

  componentDidUpdate(prevProps) {
    // Cambio di rotta (o di qualunque chiave scelta dall'area): si riparte.
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return this.props.renderFallback({ error: this.state.error, reset: this.reset });
    }
    return this.props.children;
  }
}

function TechnicalDetail({ error }) {
  if (!SHOW_TECHNICAL_DETAIL) return null;
  // Solo in sviluppo: il testo dell'eccezione aiuta chi sviluppa, non l'utente.
  const text = error instanceof Error ? error.message : String(error);
  return text ? <pre className="error-boundary__detail">{text}</pre> : null;
}

const reloadPage = () => window.location.reload();

function ErrorPage({ title, error, onHome }) {
  return (
    <div className="error-boundary error-boundary--app" role="alert">
      <div className="error-boundary__card">
        <div className="error-boundary__icon" aria-hidden="true">⚠️</div>
        <h2 className="error-boundary__title">{title}</h2>
        <p className="error-boundary__message">
          Si è verificato un errore imprevisto. Ricarica la pagina o torna alla
          home; se il problema si ripete, contatta l&apos;amministratore.
        </p>
        <TechnicalDetail error={error} />
        <div className="error-boundary__actions">
          <button
            type="button"
            className="error-boundary__btn error-boundary__btn--primary"
            onClick={reloadPage}
          >
            Ricarica
          </button>
          <button
            type="button"
            className="error-boundary__btn error-boundary__btn--secondary"
            onClick={onHome}
          >
            Torna alla home
          </button>
        </div>
      </div>
    </div>
  );
}

/** Ultima istanza, fuori dal Router. */
export function AppErrorBoundary({ children }) {
  return (
    <AreaErrorBoundary
      area="app"
      renderFallback={({ error }) => (
        <ErrorPage
          title="Plenora — Errore"
          error={error}
          onHome={() => window.location.assign('/')}
        />
      )}
    >
      {children}
    </AreaErrorBoundary>
  );
}

/** Dentro il Router: una pagina che va in errore non porta giu' l'app. */
export function RouteErrorBoundary({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <AreaErrorBoundary
      area="pagina"
      resetKey={location.pathname}
      renderFallback={({ error, reset }) => (
        <ErrorPage
          title="Questa pagina ha incontrato un errore"
          error={error}
          onHome={() => {
            // Cambiando rotta il boundary si azzera da solo (resetKey);
            // se la pagina in errore e' gia' la home si riprova qui.
            if (location.pathname === '/') reset();
            else navigate('/');
          }}
        />
      )}
    >
      {children}
    </AreaErrorBoundary>
  );
}

export function CanvasErrorBoundary({ children }) {
  return (
    <AreaErrorBoundary
      area="canvas"
      renderFallback={({ error, reset }) => (
        <div className="error-boundary error-boundary--canvas" role="alert">
          <div className="error-boundary__icon" aria-hidden="true">⚠️</div>
          <h3 className="error-boundary__title">Errore nel canvas</h3>
          <p className="error-boundary__message">
            Si è verificato un errore imprevisto nel canvas.
          </p>
          <TechnicalDetail error={error} />
          <div className="error-boundary__actions">
            <button
              type="button"
              className="error-boundary__btn error-boundary__btn--primary"
              onClick={reset}
            >
              Ricarica canvas
            </button>
            <button
              type="button"
              className="error-boundary__btn error-boundary__btn--secondary"
              onClick={reloadPage}
            >
              Ricarica pagina
            </button>
          </div>
        </div>
      )}
    >
      {children}
    </AreaErrorBoundary>
  );
}

export function ModalErrorBoundary({ children }) {
  return (
    <AreaErrorBoundary
      area="pannello"
      renderFallback={({ error, reset }) => (
        <div className="error-boundary error-boundary--modal" role="alert">
          <div className="error-boundary__icon" aria-hidden="true">⚠️</div>
          <p className="error-boundary__message">
            Si è verificato un errore imprevisto nel pannello.
          </p>
          <TechnicalDetail error={error} />
          <button
            type="button"
            className="error-boundary__btn error-boundary__btn--secondary"
            onClick={reset}
          >
            Chiudi
          </button>
        </div>
      )}
    >
      {children}
    </AreaErrorBoundary>
  );
}

export default CanvasErrorBoundary;
