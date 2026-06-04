/**
 * index.jsx — Entry point React
 *
 * Monta l'applicazione su #root. Importa i CSS globali.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/App.css';

// #35: Sentry FE error tracking — attivo SOLO se VITE_SENTRY_DSN è configurato
// a build-time. Senza DSN è un no-op totale: import dinamico, quindi il bundle
// Sentry non viene nemmeno caricato. sendDefaultPii=false (GDPR-safe).
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN;
if (SENTRY_DSN) {
  import('@sentry/react')
    .then((Sentry) => {
      Sentry.init({
        dsn: SENTRY_DSN,
        environment: import.meta.env.MODE,
        tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || 0.1),
        sendDefaultPii: false,
      });
    })
    .catch(() => { /* tracking non bloccante: se Sentry non carica, l'app prosegue */ });
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);