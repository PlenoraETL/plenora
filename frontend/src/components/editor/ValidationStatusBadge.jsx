/**
 * ValidationStatusBadge.jsx - Badge toolbar post-validazione.
 *
 * Legge `lastValidationSummary` dallo store. Dopo una validation run:
 *  - nessun errore/warning: mostra "Canvas OK" (dimmed).
 *  - errori/warning: mostra conteggi ("2 errori - 1 avviso").
 *  - click: ri-esegue validazione e apre il modal (via `onValidate`).
 *
 * Si svuota su user edit (auto-clear via `dirtyAndClearHighlights`).
 */
import React from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import './ValidationStatusBadge.css';

export default function ValidationStatusBadge() {
  const summary = useCanvasStore(s => s.lastValidationSummary);

  if (!summary) return null;

  const { errorCount, warningCount } = summary;
  const hasProblems = errorCount > 0 || warningCount > 0;

  const handleClick = () => {
    useCanvasStore.getState().editorActionsRef.current.onValidate?.();
  };

  if (!hasProblems) {
    return (
      <button
        type="button"
        className="validation-badge validation-badge--ok"
        onClick={handleClick}
        title="Ultima validazione: nessun problema"
      >
        Canvas OK
      </button>
    );
  }

  const parts = [];
  if (errorCount > 0) parts.push(`${errorCount} error${errorCount > 1 ? 'i' : 'e'}`);
  if (warningCount > 0) parts.push(`${warningCount} avvis${warningCount > 1 ? 'i' : 'o'}`);

  return (
    <button
      type="button"
      className={`validation-badge ${errorCount > 0 ? 'validation-badge--error' : 'validation-badge--warning'}`}
      onClick={handleClick}
      title="Clicca per ri-validare e vedere i dettagli"
    >
      {parts.join(' \u00B7 ')}
    </button>
  );
}
