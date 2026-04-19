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

export const authBus = {
  onUnauthorized: null,

  /** Registra un listener logout. Ritorna la funzione di unsubscribe. */
  addLogoutListener(fn) {
    logoutListeners.add(fn);
    return () => logoutListeners.delete(fn);
  },

  /** Chiamato da AuthContext.logout() — notifica tutti i listener registrati. */
  emitLogout() {
    logoutListeners.forEach(fn => fn());
  },
};
