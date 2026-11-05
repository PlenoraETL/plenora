/**
 * Unico boundary delle variabili compilate da Vite.
 *
 * Tutto il resto del frontend importa valori tipati da questo modulo: evita
 * letture `import.meta.env` sparse e rende evidente cosa finisce nel browser.
 */

function sampleRate(rawValue) {
  const value = Number(rawValue ?? 0.1);
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : 0.1;
}

export const FRONTEND_ENV = Object.freeze({
  // In produzione il default e' same-origin: il reverse proxy espone /api.
  // Un URL assoluto resta disponibile solo per deployment multi-host espliciti.
  apiBaseUrl: (import.meta.env.VITE_API_URL || '').trim().replace(/\/+$/, ''),
  mode: import.meta.env.MODE,
  // Build di sviluppo: pagine di errore con il testo tecnico e devtools
  // degli store (R10.8) solo qui.
  isDev: Boolean(import.meta.env.DEV),
  sentryDsn: import.meta.env.VITE_SENTRY_DSN || '',
  sentryTracesSampleRate: sampleRate(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE),
  // I mock non devono poter essere inclusi accidentalmente in una build prod.
  useMocks: import.meta.env.DEV && import.meta.env.VITE_USE_MOCKS === 'true',
});
