/**
 * ErrorBoundary.jsx — Componente class per cattura errori React
 *
 * React richiede un class component per implementare componentDidCatch.
 * Fornisce due varianti:
 *
 *   <CanvasErrorBoundary>   — Avvolge il canvas degli editor. In caso di crash
 *     mostra un riquadro di errore inline con pulsante Ricarica senza perdere
 *     la navigazione.
 *
 *   <AppErrorBoundary>      — Avvolge l'intera applicazione. Fallback di ultima
 *     istanza per errori non catturati da CanvasErrorBoundary.
 *
 * Uso:
 *   // In EstraiEditor / ManipolaEditor / ArricchisciEditor:
 *   <CanvasErrorBoundary>
 *     <ReactFlow ... />
 *   </CanvasErrorBoundary>
 *
 *   // In App.jsx:
 *   <AppErrorBoundary>
 *     <RouterProvider ... />
 *   </AppErrorBoundary>
 */
import React from 'react';
import './ErrorBoundary.css';

export class CanvasErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[CanvasErrorBoundary]', error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary error-boundary--canvas">
          <div className="error-boundary__icon">⚠️</div>
          <h3 className="error-boundary__title">Errore nel canvas</h3>
          <p className="error-boundary__message">
            {this.state.error?.message || 'Si è verificato un errore imprevisto nel canvas.'}
          </p>
          <div className="error-boundary__actions">
            <button
              type="button"
              className="error-boundary__btn error-boundary__btn--primary"
              onClick={this.handleReset}
            >
              Ricarica canvas
            </button>
            <button
              type="button"
              className="error-boundary__btn error-boundary__btn--secondary"
              onClick={() => window.location.reload()}
            >
              Ricarica pagina
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[AppErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary error-boundary--app">
          <div className="error-boundary__card">
            <div className="error-boundary__icon">🔴</div>
            <h2 className="error-boundary__title">Plenora — Errore critico</h2>
            <p className="error-boundary__message">
              L&apos;applicazione ha incontrato un errore imprevisto.
              Salva il tuo lavoro e ricarica la pagina.
            </p>
            {this.state.error?.message && (
              <pre className="error-boundary__detail">
                {this.state.error.message}
              </pre>
            )}
            <button
              type="button"
              className="error-boundary__btn error-boundary__btn--primary"
              onClick={() => window.location.reload()}
            >
              Ricarica applicazione
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export class ModalErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[ModalErrorBoundary]', error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary error-boundary--modal">
          <div className="error-boundary__icon">⚠️</div>
          <p className="error-boundary__message">
            Errore nel pannello: {this.state.error?.message || 'errore imprevisto'}
          </p>
          <button
            type="button"
            className="error-boundary__btn error-boundary__btn--secondary"
            onClick={this.handleReset}
          >
            Chiudi
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default CanvasErrorBoundary;
