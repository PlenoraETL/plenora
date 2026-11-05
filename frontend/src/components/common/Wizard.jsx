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
 * Regole (Fase 5, T4):
 *   - R6.2 si va avanti (Avanti o barra degli step) solo attraverso step
 *     validi; Completa controlla TUTTI gli step (`stepError`);
 *   - R6.3 un solo invio: `onComplete` puo' restituire una promise; finche'
 *     non finisce, Completa, Indietro, la barra, ×, Esc e overlay sono spenti;
 *   - R6.4/N7 chiudere (Esc, overlay, ×, Annulla) con dati inseriti chiede
 *     conferma; l'overlay chiude solo con un clic iniziato e finito fuori
 *     (stack unico delle finestre, hooks/useDialog);
 *   - tornando indietro lo stato resta (e' del chiamante, non si azzera).
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
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import useDialog from '../../hooks/useDialog';
import { useConfirmModal } from '../../hooks/useConfirmModal';
import ConfirmDialog from './ConfirmDialog';
import './Wizard.css';

export const STEP_INCOMPLETE = 'Completa questo passo prima di proseguire.';

/**
 * Errore di uno step (null = valido): il messaggio di `validate()` o
 * `canAdvance === false`. Regola UNICA per Avanti, salto dalla barra e Completa.
 */
export function stepError(s) {
  if (!s) return null;
  if (typeof s.validate === 'function') {
    const err = s.validate();
    if (err) return err;
  }
  if (s.canAdvance === false) return STEP_INCOMPLETE;
  return null;
}

/** Primo step non valido prima di `upTo` (escluso), o -1. */
export function firstInvalidStep(steps, upTo) {
  for (let k = 0; k < Math.min(upTo, steps.length); k += 1) {
    if (stepError(steps[k])) return k;
  }
  return -1;
}

export const WIZARD_DISCARD_DIALOG = {
  title: 'Chiudere senza salvare?',
  message: 'I dati inseriti in questa procedura andranno persi.',
  confirmText: 'Chiudi senza salvare',
  cancelText: 'Continua a compilare',
  variant: 'warning',
};

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
  // footer={({ requestClose, busy }) => ...}: Annulla del footer proprio passa
  // da requestClose (stessa conferma di ×/Esc con dati inseriti, R6.4).
  footer,
  closeOnEsc = true,
  closeOnOverlay = true,
  // Invio in corso deciso dal chiamante (es. upload): come `completing`.
  busy = false,
  // Dati inseriti: chiudere chiede conferma. Default: l'utente ha scritto o
  // scelto qualcosa nella finestra, oppure e' oltre il primo step.
  dirty,
  className = '',
}) {
  const [stepErrorText, setStepErrorText] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [touched, setTouched] = useState(false);
  const discard = useConfirmModal();

  useEffect(() => { if (isOpen) setTouched(false); }, [isOpen]);
  // Errore di step: si azzera a ogni cambio step (gestito centralmente qui,
  // non piu' duplicato dentro ogni step content), salvo quello dello step
  // non valido su cui il wizard si e' appena fermato.
  const pendingErrorRef = useRef(null);
  useEffect(() => {
    setStepErrorText(pendingErrorRef.current);
    pendingErrorRef.current = null;
  }, [step]);

  const isBusy = Boolean(busy || completing || submitting);
  const hasInput = dirty !== undefined ? Boolean(dirty) : (touched || step > 0);
  const confirmDiscard = discard.confirm;
  const beforeClose = useCallback(
    () => (hasInput ? confirmDiscard(WIZARD_DISCARD_DIALOG) : true),
    [hasInput, confirmDiscard],
  );
  const dialog = useDialog({ isOpen, onClose, closeOnEsc, closeOnOverlay, busy: isBusy, beforeClose });

  if (!isOpen) return null;

  const current = steps[step] || {};
  const isLast = step >= steps.length - 1;

  const show = (i, err = null) => {
    if (i === step) { setStepErrorText(err); return; }
    pendingErrorRef.current = err;
    onStepChange(i);
  };
  // R6.2: avanti (anche dalla barra degli step) solo attraverso step validi;
  // il primo non valido si apre con il suo errore.
  const goTo = (i) => {
    if (isBusy || i === step) return;
    if (i < step) { show(i); return; }
    const bad = firstInvalidStep(steps, i);
    if (bad !== -1) { show(bad, stepError(steps[bad])); return; }
    show(i);
  };
  const goBack = () => (step === 0 ? dialog.requestClose('button') : goTo(step - 1));
  const goNext = () => goTo(step + 1);
  // R6.3: un solo invio alla volta; Completa solo con TUTTI gli step validi.
  const complete = async () => {
    if (submittingRef.current || isBusy || completeDisabled) return;
    const bad = firstInvalidStep(steps, steps.length);
    if (bad !== -1) { show(bad, stepError(steps[bad])); return; }
    submittingRef.current = true;
    setSubmitting(true);
    try {
      await onComplete?.();
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };
  // Qualsiasi modifica dentro la finestra la rende "con dati inseriti".
  const markTouched = () => { if (!touched) setTouched(true); };

  return createPortal(
    <>
      <div className={`wizard__overlay ${className}`} {...dialog.overlayProps}>
        <div
          ref={dialog.ref}
          className="wizard__dialog"
          {...dialog.dialogProps}
          onChangeCapture={markTouched}
          onInputCapture={markTouched}
        >
          <div className="wizard__header">
            <div className="wizard__header-left">
              {icon && <span className="wizard__header-icon">{icon}</span>}
              <div className="wizard__title-group">
                <div className="wizard__title" id={dialog.titleId}>{title}</div>
                {subtitle && <div className="wizard__subtitle">{subtitle}</div>}
              </div>
            </div>
            <button type="button" className="wizard__close" {...dialog.closeButtonProps}>✕</button>
          </div>

          <div className="wizard__steps">
            {steps.map((s, i) => (
              <button
                type="button"
                key={i}
                className={`wizard__step${step === i ? ' wizard__step--active' : ''}${step > i ? ' wizard__step--done' : ''}`}
                onClick={() => (allowJump ? goTo(i) : undefined)}
                disabled={!allowJump || isBusy}
                aria-current={step === i ? 'step' : undefined}
              >
                <span className="wizard__step-num">{step > i ? '✓' : i + 1}</span>
                <span className="wizard__step-label">{s.label}</span>
              </button>
            ))}
          </div>

          <div className="wizard__body">
            {stepErrorText && (
              <div className="wizard__step-error" role="alert">
                <span>⚠️</span><span>{stepErrorText}</span>
              </div>
            )}
            {current.content}
          </div>

          {footer !== null && (
          <div className="wizard__footer">
            {footer !== undefined ? (typeof footer === 'function' ? footer({ requestClose: () => dialog.requestClose('button'), busy: isBusy }) : footer) : (
              <>
                <button type="button" className="wizard__btn wizard__btn--ghost" onClick={goBack} disabled={isBusy}>
                  {step === 0 ? cancelLabel : '← Indietro'}
                </button>
                {isLast ? (
                  <button
                    type="button"
                    className="wizard__btn wizard__btn--ok"
                    onClick={complete}
                    disabled={completeDisabled || isBusy}
                  >
                    {isBusy ? 'Attendere…' : completeLabel}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="wizard__btn wizard__btn--primary"
                    onClick={goNext}
                    disabled={current.canAdvance === false || isBusy}
                  >
                    Avanti →
                  </button>
                )}
              </>
            )}
          </div>
          )}
        </div>
      </div>
      <ConfirmDialog {...discard} />
    </>,
    document.body
  );
}
