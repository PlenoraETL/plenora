/**
 * NotFoundPage — pagina 404 (Fase 5, T4, R7.5): un indirizzo che non esiste
 * lo dice, invece di riportare in silenzio alla home.
 */
import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../components/common/ErrorBoundary.css';

export default function NotFoundPage() {
  const location = useLocation();
  return (
    <div className="error-boundary error-boundary--app" role="alert">
      <div className="error-boundary__card">
        <div className="error-boundary__icon" aria-hidden="true">🧭</div>
        <h2 className="error-boundary__title">Pagina non trovata</h2>
        <p className="error-boundary__message">
          L&apos;indirizzo <code>{location.pathname}</code> non corrisponde a nessuna pagina di Plenora.
        </p>
        <div className="error-boundary__actions">
          <Link className="error-boundary__btn error-boundary__btn--primary" to="/">Torna alla home</Link>
        </div>
      </div>
    </div>
  );
}
