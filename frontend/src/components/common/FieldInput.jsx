/**
 * FieldInput — il campo unico con buffer locale (Fase 5 T5, R8.1-R8.4, N4, N12).
 *
 * `<FieldInput codec={integer({ min: 1 })} value={n} onCommit={setN} />`
 *
 * Tiene il testo mentre si scrive (hooks/useFieldBuffer), converte con UN
 * convertitore per tipo (utils/fieldCodecs), mostra l'errore del campo invece
 * di sostituire in silenzio un valore non valido. `as="textarea"` per i testi
 * lunghi. Le altre props vanno all'elemento (className, placeholder,
 * disabled, aria-*...).
 *
 * <FieldScope scope={useFieldScope()}> intorno a un form fa sapere ad
 * Avanti/Salva se c'e' un campo con testo non valido (il Wizard lo fa da solo).
 */
import React, { useId } from 'react';
import { FieldScopeContext, useFieldBuffer } from '../../hooks/useFieldBuffer';
import './FieldInput.css';

export function FieldScope({ scope, children }) {
  return <FieldScopeContext.Provider value={scope}>{children}</FieldScopeContext.Provider>;
}

export default function FieldInput({
  codec,
  value,
  onCommit,
  commitOn = 'change',
  validate,
  as = 'input',
  errorClassName = '',
  type,
  ...rest
}) {
  const field = useFieldBuffer({ value, codec, onCommit, commitOn, validate });
  const errorId = useId();
  const Tag = as;
  const inputType = as === 'input'
    ? (type && type !== 'number' ? type : 'text')
    : undefined;
  return (
    <>
      <Tag
        {...rest}
        {...(inputType ? { type: inputType } : {})}
        inputMode={codec.inputMode || rest.inputMode}
        {...field.inputProps}
        onBlur={(e) => { field.inputProps.onBlur(); rest.onBlur?.(e); }}
        onKeyDown={(e) => { field.inputProps.onKeyDown(e); rest.onKeyDown?.(e); }}
        aria-invalid={field.showError || undefined}
        aria-describedby={field.showError ? errorId : rest['aria-describedby']}
      />
      {field.showError && (
        <span id={errorId} className={`field-input__error ${errorClassName}`.trim()} role="alert">
          {field.error}
        </span>
      )}
    </>
  );
}
