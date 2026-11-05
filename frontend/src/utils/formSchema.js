/**
 * formSchema.js — form dichiarativi (Fase 5 T5, R8.5, R8.10, R8.13).
 *
 * Uno schema e' un elenco di campi; rendering, validazione e payload vengono
 * dalla STESSA fonte, quindi quello che l'utente vede e' quello che si invia:
 *
 *   { name, label, codec, required?, visible?, secret?, control?, options?,
 *     placeholder?, hint?, step? }
 *
 *   - `visible(values, ctx)`: il campo esiste solo se visibile (un campo di un
 *     altro tipo rimasto nello stato non si valida e non si invia);
 *   - `required` (bool o funzione): vuoto = errore `requiredMessage`
 *     (default «Campo obbligatorio»);
 *   - `codec` (utils/fieldCodecs): il testo dello stato -> il valore inviato;
 *     un testo non valido e' l'errore del campo, mai un default;
 *   - `secret`: in modifica un segreto vuoto vuol dire "invariato" e non si
 *     invia; scritto, si invia.
 *
 * I valori dello stato del form sono il testo dei campi (o il booleano delle
 * caselle): il buffer e' lo stato stesso.
 */
import { text as textCodec } from './fieldCodecs';

const resolve = (x, values, ctx) => (typeof x === 'function' ? x(values, ctx) : x);
const isEmpty = (v) => v === null || v === undefined || (typeof v === 'string' && v.trim() === '')
  || (Array.isArray(v) && v.length === 0);

const codecOf = (field) => field.codec || textCodec();

/** Campi visibili con questi valori. */
export const visibleFields = (fields, values, ctx = {}) =>
  fields.filter(f => resolve(f.visible ?? true, values, ctx) !== false);

/** Valore convertito di un campo: `{ value, error }`. */
function fieldResult(field, values, ctx = {}) {
  const raw = values[field.name];
  if (typeof raw === 'boolean') return { value: raw, error: null };
  const required = Boolean(resolve(field.required ?? false, values, ctx));
  const REQUIRED = field.requiredMessage || 'Campo obbligatorio';
  const secretKept = field.secret && ctx.mode === 'edit' && isEmpty(raw);
  if (isEmpty(raw) && (secretKept || !required)) return { value: undefined, error: null, empty: true };
  if (isEmpty(raw) && required) return { value: undefined, error: REQUIRED };
  const r = codecOf(field).parse(raw);
  if (r.error === 'Campo obbligatorio') return { value: undefined, error: REQUIRED };
  if (r.error) return r;
  if (required && isEmpty(r.value)) return { value: undefined, error: REQUIRED };
  return r;
}

/** Errori dei campi visibili: `{ [name]: messaggio }` (vuoto = valido). */
export function formErrors(fields, values, ctx = {}) {
  const errors = {};
  visibleFields(fields, values, ctx).forEach((f) => {
    const { error } = fieldResult(f, values, ctx);
    if (error) errors[f.name] = error;
    else if (typeof f.check === 'function') {
      const extra = f.check(values, ctx);
      if (extra) errors[f.name] = extra;
    }
  });
  return errors;
}

/**
 * Payload dai campi visibili. Creazione: un campo facoltativo vuoto non si
 * invia. Modifica (`ctx.mode === 'edit'`): un campo vuoto si invia come
 * `clearValue` del campo (default '') per poterlo svuotare; un segreto vuoto
 * non si invia (invariato).
 */
export function formPayload(fields, values, ctx = {}) {
  const payload = {};
  visibleFields(fields, values, ctx).forEach((f) => {
    if (f.payload === false) return;
    const r = fieldResult(f, values, ctx);
    if (r.error) throw new Error(`Campo non valido: ${f.name}`);
    if (r.empty) {
      if (f.secret || ctx.mode !== 'edit') return;
      payload[f.name] = f.clearValue !== undefined ? f.clearValue : '';
      return;
    }
    payload[f.name] = r.value;
  });
  return payload;
}

/** Testo iniziale dello stato da un oggetto salvato (secret sempre vuoti). */
export function initialValues(fields, source = {}, defaults = {}) {
  const values = {};
  fields.forEach((f) => {
    if (f.secret) { values[f.name] = ''; return; }
    const v = source?.[f.name] ?? defaults[f.name] ?? f.default;
    if (f.control === 'checkbox') { values[f.name] = Boolean(v ?? false); return; }
    values[f.name] = v === undefined || v === null ? '' : codecOf(f).format(v);
  });
  return values;
}
