/**
 * index.jsx — Entry point React
 *
 * Monta l'applicazione su #root. Importa i CSS globali.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { FRONTEND_ENV } from './config/runtimeEnv';
import { installChunkReload } from './utils/chunkReload';
import './styles/App.css';

// Dopo un deploy: un editor della build vecchia non si trova piu' -> si
// ricarica la pagina una volta invece della schermata di errore critico.
installChunkReload();

// #35: Sentry FE error tracking — attivo SOLO se VITE_SENTRY_DSN è configurato
// a build-time. Senza DSN è un no-op totale: import dinamico, quindi il bundle
// Sentry non viene nemmeno caricato. sendDefaultPii=false (GDPR-safe).
const SENTRY_DSN = FRONTEND_ENV.sentryDsn;
if (SENTRY_DSN) {
  import('@sentry/react')
    .then((Sentry) => {
      Sentry.init({
        dsn: SENTRY_DSN,
        environment: FRONTEND_ENV.mode,
        tracesSampleRate: FRONTEND_ENV.sentryTracesSampleRate,
        sendDefaultPii: false,
      });
    })
    .catch(() => { /* tracking non bloccante: se Sentry non carica, l'app prosegue */ });
}

async function bootstrap() {
  // #50: mock server dev (MSW) — opt-in via VITE_USE_MOCKS, import dinamico,
  // mai in produzione (flag non impostato nel build dell'immagine).
  if (FRONTEND_ENV.useMocks) {
    const { worker } = await import('./mocks/browser');
    await worker.start({ onUnhandledRequest: 'bypass' });
  }
  ReactDOM.createRoot(document.getElementById('root')).render(<App />);
}

bootstrap();
