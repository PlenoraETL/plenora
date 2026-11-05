/**
 * errorReporting.js — invio a Sentry degli errori catturati dagli Error
 * Boundary (R5.7).
 *
 * In produzione React non rilancia un errore catturato da un boundary, quindi
 * l'integrazione globale di Sentry non lo vede: ogni boundary chiama
 * `reportError` in `componentDidCatch`. Sentry si carica solo se
 * VITE_SENTRY_DSN e' configurato (index.jsx): finche' non c'e' un client,
 * `reportError` scrive solo in console.
 */

let reporter = null;

/** Registrato da index.jsx dopo `Sentry.init`. */
export function setErrorReporter(client) {
  reporter = client && typeof client.captureException === 'function' ? client : null;
}

/**
 * @param {unknown} error
 * @param {{componentStack?: string}} [info]
 * @param {string} [area] - quale boundary l'ha catturato (app, pagina, canvas, pannello)
 */
export function reportError(error, info, area = 'app') {
  console.error(`[ErrorBoundary:${area}]`, error, info?.componentStack);
  if (!reporter) return;
  try {
    reporter.captureException(error, {
      tags: { error_boundary: area },
      contexts: { react: { componentStack: info?.componentStack || '' } },
    });
  } catch {
    // Il tracciamento non deve mai rompere la pagina di errore.
  }
}
