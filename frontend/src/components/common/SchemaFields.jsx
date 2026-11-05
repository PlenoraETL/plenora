/**
 * SchemaFields — rendering dei campi di uno schema (utils/formSchema).
 *
 * Mostra SOLO i campi visibili, ciascuno con `name` = nome del campo (lo
 * stesso del payload), etichetta, controllo, suggerimento ed errore. Il
 * chiamante passa lo stato (testo dei campi), gli errori da mostrare e le
 * classi CSS della pagina.
 */
import React from 'react';
import { visibleFields } from '../../utils/formSchema';
import './FieldInput.css';

const DEFAULT_CLASSES = {
  field: 'schema-field', label: 'schema-field__label', input: 'schema-field__input',
  textarea: 'schema-field__textarea', select: 'schema-field__select', hint: 'schema-field__hint',
  check: 'schema-field__check', grid: 'schema-fields',
};

export default function SchemaFields({ fields, values, ctx = {}, errors = {}, onChange, classes = {}, disabled = false }) {
  const c = { ...DEFAULT_CLASSES, ...classes };
  return (
    <div className={c.grid}>
      {visibleFields(fields, values, ctx).map((f) => {
        const id = `schema-${f.name}`;
        const value = values[f.name];
        const placeholder = typeof f.placeholder === 'function' ? f.placeholder(values, ctx) : f.placeholder;
        const required = typeof f.required === 'function' ? f.required(values, ctx) : f.required;
        const err = errors[f.name];
        const common = {
          id, name: f.name, disabled, placeholder,
          'aria-invalid': err ? true : undefined,
          'aria-describedby': err ? `${id}-error` : undefined,
        };
        if (f.control === 'checkbox') {
          return (
            <label key={f.name} className={c.check}>
              <input type="checkbox" {...common} checked={Boolean(value)} onChange={e => onChange(f.name, e.target.checked)} />
              <span>{f.label}</span>
            </label>
          );
        }
        let control;
        if (f.control === 'select') {
          control = (
            <select className={c.select} {...common} value={value ?? ''} onChange={e => onChange(f.name, e.target.value)}>
              {(f.options || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          );
        } else if (f.control === 'textarea') {
          control = <textarea className={c.textarea} rows={f.rows || 4} {...common} value={value ?? ''} onChange={e => onChange(f.name, e.target.value)} />;
        } else {
          control = (
            <input
              className={c.input}
              type={f.control === 'password' ? 'password' : 'text'}
              inputMode={f.codec?.inputMode}
              {...common}
              value={value ?? ''}
              onChange={e => onChange(f.name, e.target.value)}
            />
          );
        }
        return (
          <div key={f.name} className={`${c.field}${f.width ? ` ${c.field}--${f.width}` : ''}`}>
            <label className={c.label} htmlFor={id}>{f.label}{required ? ' *' : ''}</label>
            {control}
            {f.hint && <span className={c.hint}>{f.hint}</span>}
            {err && <span id={`${id}-error`} className="field-input__error" role="alert">{err}</span>}
          </div>
        );
      })}
    </div>
  );
}
