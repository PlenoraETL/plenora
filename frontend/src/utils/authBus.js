/**
 * authBus.js — Canale di comunicazione per eventi di autenticazione
 *
 * Problema risolto:
 *   client.js (axios) non ha accesso al contesto React, quindi non può
 *   chiamare logout() né useNavigate(). La soluzione precedente usava
 *   window.location.href che bypassa React Router e non pulisce lo store.
 *
 * Soluzione:
 *   authBus è un oggetto semplice con un callback registrabile.
 *   AuthContext registra onUnauthorized al mount.
 *   client.js chiama authBus.onUnauthorized() quando riceve 401.
 *
 * Utilizzo:
 *   // AuthContext.jsx
 *   authBus.onUnauthorized = () => { logout(); navigate('/login'); };
 *
 *   // client.js
 *   if (status === 401) authBus.onUnauthorized?.();
 */
/**
 * onLogout: chiamato da AuthContext.logout() per permettere ai moduli
 * con cache in-memory (es. TransformNode.registryCache) di invalidarsi.
 */
const logoutListeners = new Set();
// Prima di togliere l'utente (uscita o 401): chi ha lavoro in sospeso lo
// mette al sicuro finche' l'utente e' ancora noto (copia di sicurezza).
const beforeLogoutListeners = new Set();

export const authBus = {
  onUnauthorized: null,

  /** Registra un listener logout. Ritorna la funzione di unsubscribe. */
  addLogoutListener(fn) {
    logoutListeners.add(fn);
    return () => logoutListeners.delete(fn);
  },

  /** Registra un listener chiamato PRIMA di togliere l'utente. */
  addBeforeLogoutListener(fn) {
    beforeLogoutListeners.add(fn);
    return () => beforeLogoutListeners.delete(fn);
  },

  /** Chiamato da AuthContext.logout() come prima cosa. */
  emitBeforeLogout() {
    beforeLogoutListeners.forEach(fn => { try { fn(); } catch { /* non blocca l'uscita */ } });
  },

  /** Chiamato da AuthContext.logout() — notifica tutti i listener registrati. */
  emitLogout() {
    logoutListeners.forEach(fn => fn());
  },
};
