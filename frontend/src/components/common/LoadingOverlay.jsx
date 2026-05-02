/**
 * LoadingOverlay - Componenti per stati di caricamento
 *
 * Fornisce:
 * - LoadingOverlay: overlay a tutto schermo o contenitore (default export)
 * - LoadingSpinner: spinner standalone usato internamente da LoadingOverlay
 */

import React from 'react';
import './LoadingOverlay.css';

/**
 * Overlay di caricamento (copre il contenitore padre)
 */
export function LoadingOverlay({
  loading = true,
  message = 'Caricamento...',
  fullScreen = false,
  transparent = false
}) {
  if (!loading) return null;

  return (
    <div className={`loading-overlay ${fullScreen ? 'loading-overlay--fullscreen' : ''} ${transparent ? 'loading-overlay--transparent' : ''}`}>
      <div className="loading-overlay__content">
        <LoadingSpinner size="lg" />
        {message && <p className="loading-overlay__message">{message}</p>}
      </div>
    </div>
  );
}

/**
 * Spinner standalone (usato internamente da LoadingOverlay).
 */
export function LoadingSpinner({ size = 'md', color = 'primary' }) {
  return (
    <div className={`loading-spinner loading-spinner--${size} loading-spinner--${color}`}>
      <div className="loading-spinner__circle"></div>
    </div>
  );
}

export default LoadingOverlay;
