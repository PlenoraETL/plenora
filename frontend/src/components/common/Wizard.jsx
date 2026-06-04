/**
 * Wizard — chrome condiviso per i modal multi-step (classe "wizard").
 *
 * Estrae il chrome che i 5 wizard del FE reimplementavano a mano (overlay +
 * dialog a11y + step-bar + body scrollabile + footer Indietro/Avanti/Completa
 * con validazione per-step). Il CONTENUTO di ogni step e la logica di stato
 * restano del wizard chiamante: qui passa solo `steps` (label + content +
 * gating) e le callback di navigazione/completamento.
 *
 * Complementare a ModalWrapper (che resta la base dei modal NON-stepper).
 *
 * @example
 * <Wizard
 *   isOpen={open} onClose={onClose} title="Nuovo Connettore"
 *   step={step} onStepChange={setStep}
 *   steps={[
 *     { label:'Tipo', content:<Step0 .../> },
 *     { label:'Info', content:<Step1 .../>, canAdvance: !!name && !!url },
 *     { label:'Parametri', content:<Step2 .../>, validate: () => hasFile ? null : 'Aggiungi un file' },
 *   ]}
 *   onComplete={handleSave} completeLabel="💾 Salva" completeDisabled={!name}
 * />
 */
import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import useDialogFocus from '../../hooks/useDialogFocus';
import './Wizard.css';

export default function Wizard({
  isOpen,
  onClose,
  title,
  subtitle,
  icon,
  step,
  onStepChange,
  steps = [],
  onComplete,
  completeLabel = 'Salva',
  completeDisabled = false,
  completing = false,
  cancelLabel = 'Annulla',
  allowJump = true,
  // footer: override del footer di default (Indietro/Avanti/Completa). Per i
  // wizard "bespoke" con navigazione side-effectful o azioni non standard
  // (es. UploadModal): riusano chrome + step-bar + a11y, footer proprio.
  // footer={null} -> nessuna barra footer (bottoni nel body).
  footer,
  closeOnEsc = true,
  closeOnOverlay = true,
  className = '',
}) {
  const dialogRef = useRef(null);
  const [stepError, setStepError] = useState(null);
  useDialogFocus(isOpen, onClose, dialogRef, { closeOnEsc });

  // Reset dell'errore di step ad ogni cambio step (gestito centralmente qui,
  // non piu' duplicato dentro ogni step content).
  useEffect(() => { setStepError(null); }, [step]);

  if (!isOpen) return null;

  const current = steps[step] || {};
  const isLast = step >= steps.length - 1;

  const goTo = (i) => { setStepError(null); onStepChange(i); };
  const goBack = () => (step === 0 ? onClose() : goTo(step - 1));
  const goNext = () => {
    // validate(): ritorna una stringa d'errore per BLOCCARE l'avanzamento,
    // oppure null/undefined per procedere.
    if (typeof current.validate === 'function') {
      const err = current.validate();
      if (err) { setStepError(err); return; }
    }
    goTo(step + 1);
  };

  return createPortal(
    <div className={`wizard__overlay ${className}`} onClick={closeOnOverlay ? onClose : undefined}>
      <div
        ref={dialogRef}
        className="wizard__dialog"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="wizard-title"
        tabIndex={-1}
      >
        <div className="wizard__header">
          <div className="wizard__header-left">
            {icon && <span className="wizard__header-icon">{icon}</span>}
            <div className="wizard__title-group">
              <div className="wizard__title" id="wizard-title">{title}</div>
              {subtitle && <div className="wizard__subtitle">{subtitle}</div>}
            </div>
          </div>
          <button type="button" className="wizard__close" onClick={onClose} aria-label="Chiudi">✕</button>
        </div>

        <div className="wizard__steps">
          {steps.map((s, i) => (
            <button
              type="button"
              key={i}
              className={`wizard__step${step === i ? ' wizard__step--active' : ''}${step > i ? ' wizard__step--done' : ''}`}
              onClick={() => (allowJump ? goTo(i) : undefined)}
              disabled={!allowJump}
            >
              <span className="wizard__step-num">{step > i ? '✓' : i + 1}</span>
              <span className="wizard__step-label">{s.label}</span>
            </button>
          ))}
        </div>

        <div className="wizard__body">
          {stepError && (
            <div className="wizard__step-error" role="alert">
              <span>⚠️</span><span>{stepError}</span>
            </div>
          )}
          {current.content}
        </div>

        {footer !== null && (
        <div className="wizard__footer">
          {footer !== undefined ? footer : (
            <>
              <button type="button" className="wizard__btn wizard__btn--ghost" onClick={goBack}>
                {step === 0 ? cancelLabel : '← Indietro'}
              </button>
              {isLast ? (
                <button
                  type="button"
                  className="wizard__btn wizard__btn--ok"
                  onClick={onComplete}
                  disabled={completeDisabled || completing}
                >
                  {completing ? 'Attendere…' : completeLabel}
                </button>
              ) : (
                <button
                  type="button"
                  className="wizard__btn wizard__btn--primary"
                  onClick={goNext}
                  disabled={current.canAdvance === false}
                >
                  Avanti →
                </button>
              )}
            </>
          )}
        </div>
        )}
      </div>
    </div>,
    document.body
  );
}
