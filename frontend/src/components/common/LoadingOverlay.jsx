/**
 * LoadingOverlay - Componenti per stati di caricamento
 * 
 * Fornisce:
 * - LoadingOverlay: overlay a tutto schermo o contenitore
 * - LoadingSpinner: spinner standalone
 * - LoadingButton: pulsante con stato loading
 * - LoadingInline: loading inline nel testo
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
 * Spinner standalone
 */
export function LoadingSpinner({ size = 'md', color = 'primary' }) {
  return (
    <div className={`loading-spinner loading-spinner--${size} loading-spinner--${color}`}>
      <div className="loading-spinner__circle"></div>
    </div>
  );
}

/**
 * Dots animati (alternativa allo spinner)
 */
export function LoadingDots({ size = 'md' }) {
  return (
    <div className={`loading-dots loading-dots--${size}`}>
      <span className="loading-dots__dot"></span>
      <span className="loading-dots__dot"></span>
      <span className="loading-dots__dot"></span>
    </div>
  );
}

/**
 * Skeleton loader per placeholder contenuti
 */
export function LoadingSkeleton({ 
  width = '100%', 
  height = '20px', 
  borderRadius = '4px',
  count = 1 
}) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div 
          key={i}
          className="loading-skeleton"
          style={{ width, height, borderRadius, marginBottom: i < count - 1 ? '8px' : 0 }}
        />
      ))}
    </>
  );
}

/**
 * Loading inline (per testo)
 */
export function LoadingInline({ text = 'Caricamento' }) {
  return (
    <span className="loading-inline">
      {text}
      <LoadingDots size="sm" />
    </span>
  );
}

/**
 * Wrapper per mostrare loading o contenuto
 */
export function LoadingWrapper({ loading, children, fallback = null }) {
  if (loading) {
    return fallback || <LoadingOverlay loading={true} />;
  }
  return children;
}

export default LoadingOverlay;
