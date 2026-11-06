/**
 * index.jsx — Entry point React
 *
 * Monta l'applicazione su #root. Importa i CSS globali.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { FRONTEND_ENV } from './config/runtimeEnv';
import { installDraftHousekeeping } from './utils/localDrafts';
import { setErrorReporter } from './utils/errorReporting';
// Font ospitati con l'app (P31), prima dei CSS che li usano.
import './assets/fonts/fonts.css';
import './styles/App.css';


// Copie di sicurezza: pulizia globale delle scadute e segnale di vita della
// scheda (utils/localDrafts).
installDraftHousekeeping();

// #35: Sentry FE error tracking — attivo SOLO se VITE_SENTRY_DSN è configurato
// a build-time. Senza DSN è un no-op totale: import dinamico, quindi il bundle
// Sentry non viene nemmeno caricato. sendDefaultPii=false (GDPR-safe).
// UI-4 (V3c): con la DSN si scarica DOPO il primo render, quando il browser
// e' libero (requestIdleCallback, al massimo 3 s), non insieme all'app.
const SENTRY_DSN = FRONTEND_ENV.sentryDsn;
function startErrorTracking() {
  import('@sentry/react')
    .then((Sentry) => {
      Sentry.init({
        dsn: SENTRY_DSN,
        environment: FRONTEND_ENV.mode,
        tracesSampleRate: FRONTEND_ENV.sentryTracesSampleRate,
        sendDefaultPii: false,
      });
      // Gli Error Boundary inviano a Sentry gli errori che catturano (R5.7).
      setErrorReporter(Sentry);
    })
    .catch(() => { /* tracking non bloccante: se Sentry non carica, l'app prosegue */ });
}
const whenIdle = (fn) => (typeof window.requestIdleCallback === 'function'
  ? window.requestIdleCallback(fn, { timeout: 3000 })
  : window.setTimeout(fn, 1500));

async function bootstrap() {
  // #50: mock server dev (MSW) — opt-in via VITE_USE_MOCKS, import dinamico,
  // mai in produzione (flag non impostato nel build dell'immagine).
  if (FRONTEND_ENV.useMocks) {
    const { worker } = await import('./mocks/browser');
    await worker.start({ onUnhandledRequest: 'bypass' });
  }
  ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  if (SENTRY_DSN) whenIdle(startErrorTracking);
}

bootstrap();
