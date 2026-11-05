/**
 * fieldCodecs.js — un solo convertitore per tipo di campo (Fase 5 T5, R8).
 *
 * Ogni campo di un form passa da qui tra il TESTO che l'utente scrive e il
 * VALORE che si salva. Un convertitore e' `{ kind, format, parse }`:
 *   - `format(value) -> string`   il testo da mostrare per un valore;
 *   - `parse(text) -> { value, error }`   il valore, oppure il motivo per cui
 *     il testo non e' valido (mostrato come errore del campo).
 *
 * Regola: un testo non valido NON diventa mai un default in silenzio
 * (prima `parseInt(v) || 100`: svuotando il campo tornava 100, digitando "5"
 * si otteneva "1005"). Il testo resta quello scritto, l'errore si vede, il
 * form non si invia (hooks/useFieldBuffer, utils/formSchema).
 *
 * Il test `tests/shared/fieldCodecs.contract.test.js` vieta `parseInt`,
 * `parseFloat` e `Number(x) || default` sul testo dei campi nel resto di src.
 */
import { cronProblem, normalizeCron } from './cron';
import { isKnownTimezone } from './timezones';

const ok = (value) => ({ value, error: null });
const fail = (error) => ({ value: undefined, error });
const isBlank = (text) => String(text ?? '').trim() === '';
const REQUIRED = 'Campo obbligatorio';

const rangeError = (n, min, max) => {
  if (min != null && max != null && (n < min || n > max)) return `Inserisci un valore tra ${min} e ${max}`;
  if (min != null && n < min) return `Il valore minimo è ${min}`;
  if (max != null && n > max) return `Il valore massimo è ${max}`;
  return null;
};

const blankValue = (optional, empty = null) => (optional ? ok(empty) : fail(REQUIRED));

/** Numero intero, con limiti. `optional`: vuoto = `empty` (default null). */
export function integer({ min, max, optional = false, empty = null } = {}) {
  return {
    kind: 'integer',
    inputMode: 'numeric',
    format: (v) => (v === null || v === undefined || v === '' || Number.isNaN(v) ? '' : String(v)),
    parse(text) {
      const t = String(text ?? '').trim();
      if (t === '') return blankValue(optional, empty);
      if (!/^[-+]?\d+$/.test(t)) return fail('Inserisci un numero intero');
      const n = Number(t);
      const err = rangeError(n, min, max);
      return err ? fail(err) : ok(n);
    },
  };
}

/** Numero decimale (accetta anche la virgola come separatore), con limiti. */
export function decimal({ min, max, optional = false, empty = null } = {}) {
  return {
    kind: 'decimal',
    inputMode: 'decimal',
    format: (v) => (v === null || v === undefined || v === '' || Number.isNaN(v) ? '' : String(v)),
    parse(text) {
      const t = String(text ?? '').trim();
      if (t === '') return blankValue(optional, empty);
      if (!/^[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?$/.test(t)) return fail('Inserisci un numero');
      const n = Number(t.replace(',', '.'));
      if (!Number.isFinite(n)) return fail('Inserisci un numero');
      const err = rangeError(n, min, max);
      return err ? fail(err) : ok(n);
    },
  };
}

const TRUE_WORDS = ['true', '1', 'yes', 'si', 'sì'];
const FALSE_WORDS = ['false', '0', 'no'];

/** Booleano (stesse parole del backend, eseguibili/runtime._coerce_scalar). */
export function boolean({ optional = false } = {}) {
  return {
    kind: 'boolean',
    format: (v) => (v === true ? 'true' : v === false ? 'false' : ''),
    parse(text) {
      if (typeof text === 'boolean') return ok(text);
      const t = String(text ?? '').trim().toLowerCase();
      if (t === '') return blankValue(optional);
      if (TRUE_WORDS.includes(t)) return ok(true);
      if (FALSE_WORDS.includes(t)) return ok(false);
      return fail('Scegli sì o no');
    },
  };
}

/** Testo libero, con lunghezza ed espressione regolare facoltative. */
export function text({
  optional = true, trim = true, minLength, maxLength, pattern, patternMessage, empty = '',
} = {}) {
  let re = null;
  if (pattern) {
    try { re = pattern instanceof RegExp ? pattern : new RegExp(`^(?:${pattern})$`); } catch { re = null; }
  }
  return {
    kind: 'text',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      const t = trim ? String(raw ?? '').trim() : String(raw ?? '');
      if (t === '') return blankValue(optional, empty);
      const len = Array.from(t).length;
      if (minLength != null && len < minLength) return fail(`Almeno ${minLength} caratteri`);
      if (maxLength != null && len > maxLength) return fail(`Al massimo ${maxLength} caratteri`);
      if (re && !re.test(t)) return fail(patternMessage || 'Formato non valido');
      return ok(t);
    },
  };
}

/** Espressione regolare: vuoto = nessuna; deve compilare. */
export function regex() {
  return {
    kind: 'regex',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      const t = String(raw ?? '').trim();
      if (t === '') return ok(null);
      try { new RegExp(t); } catch { return fail('Espressione regolare non valida'); }
      return ok(t);
    },
  };
}

/** Una delle opzioni date (select). */
export function oneOf(options, { optional = false } = {}) {
  const values = options.map(o => (typeof o === 'object' ? o.value : o));
  return {
    kind: 'oneOf',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      if (isBlank(raw)) return blankValue(optional);
      const match = values.find(v => String(v) === String(raw));
      return match === undefined ? fail('Opzione non valida') : ok(match);
    },
  };
}

/**
 * Elenco separato da virgole. Il testo si tiene com'e' mentre si scrive (la
 * virgola appena digitata non sparisce); il valore e' l'elenco degli
 * elementi non vuoti, ciascuno passato dal convertitore `item`.
 */
export function list({ item = text(), separator = ',', unique = true, optional = true, minItems } = {}) {
  return {
    kind: 'list',
    format: (v) => (Array.isArray(v) ? v.map(x => item.format(x)).join(`${separator} `) : ''),
    parse(raw) {
      const parts = String(raw ?? '').split(separator).map(s => s.trim()).filter(Boolean);
      if (parts.length === 0) return optional && !minItems ? ok([]) : fail(REQUIRED);
      if (minItems && parts.length < minItems) return fail(`Servono almeno ${minItems} valori`);
      const values = [];
      for (const part of parts) {
        const r = item.parse(part);
        if (r.error) return fail(`«${part}»: ${r.error}`);
        if (unique && values.some(v => String(v) === String(r.value))) return fail(`«${part}» è ripetuto`);
        values.push(r.value);
      }
      return ok(values);
    },
  };
}

/**
 * Chiave (identificatore): il testo si normalizza SOLO al commit (blur), non
 * a ogni tasto: "file_" resta "file_" mentre si scrive "file_clienti".
 * `allowed` = caratteri ammessi oltre a [a-z0-9]; `startsWithLetter` come il
 * pattern del backend.
 */
export function slugify(value, { allowed = '_', startsWithLetter = true } = {}) {
  const extra = allowed.replace(/[-\\\]^]/g, '\\$&');
  const edge = new RegExp(`^[${extra}]+|[${extra}]+$`, 'g');
  let out = String(value ?? '')
    .trim()
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(new RegExp(`[^a-z0-9${extra}]+`, 'g'), '_')
    .replace(edge, '');
  if (startsWithLetter) out = out.replace(/^[^a-z]+/, '');
  return out;
}

export function key({ allowed = '_', startsWithLetter = true, maxLength = 80, optional = true } = {}) {
  return {
    kind: 'key',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      if (isBlank(raw)) return blankValue(optional, '');
      const k = slugify(raw, { allowed, startsWithLetter });
      if (!k) return fail(startsWithLetter ? 'La chiave deve contenere almeno una lettera' : 'Chiave non valida');
      if (k.length > maxLength) return fail(`Al massimo ${maxLength} caratteri`);
      return ok(k);
    },
  };
}

/** Espressione cron (regola unica utils/cron, la stessa dello scheduler). */
export function cron() {
  return {
    kind: 'cron',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      const problem = cronProblem(raw);
      return problem ? fail(problem) : ok(normalizeCron(raw));
    },
  };
}

/**
 * Fuso orario: uno dell'elenco unico, oppure il valore gia' salvato
 * (`keep`), mostrato com'e' (utils/timezones).
 */
export function timezone({ keep } = {}) {
  return {
    kind: 'timezone',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      const t = String(raw ?? '').trim();
      if (!t) return fail(REQUIRED);
      if (isKnownTimezone(t) || (keep && t === keep)) return ok(t);
      return fail('Fuso orario non riconosciuto');
    },
  };
}

/** SRID / EPSG: "4326", "EPSG:4326", "epsg 4326" -> 4326. */
export function srid({ optional = true } = {}) {
  return {
    kind: 'srid',
    inputMode: 'numeric',
    format: (v) => (v === null || v === undefined || v === '' ? '' : String(v)),
    parse(raw) {
      const t = String(raw ?? '').trim();
      if (t === '') return blankValue(optional);
      const m = t.match(/^(?:epsg\s*[:\s]\s*)?(\d{4,6})$/i);
      if (!m) return fail('Inserisci un codice EPSG (es. 4326 o EPSG:4326)');
      const n = Number(m[1]);
      return n > 0 ? ok(n) : fail('Codice EPSG non valido');
    },
  };
}

/**
 * Nome di file senza estensione: niente separatori di percorso o caratteri
 * vietati; un'estensione tra `extensions` scritta dall'utente si toglie
 * ("x.csv" -> "x", non "x.csv.csv").
 */
export function fileStem({ extensions = [], optional = true, empty = '' } = {}) {
  const exts = extensions.map(e => String(e).toLowerCase().replace(/^\./, ''));
  return {
    kind: 'fileStem',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      let t = String(raw ?? '').trim();
      if (t === '') return blankValue(optional, empty);
      if (/[/\\:*?"<>|]/.test(t)) return fail('Il nome non può contenere / \\ : * ? " < > |');
      const dot = t.lastIndexOf('.');
      if (dot > 0 && exts.includes(t.slice(dot + 1).toLowerCase())) t = t.slice(0, dot).trim();
      if (t === '' || /^\.+$/.test(t)) return fail('Nome file non valido');
      return ok(t);
    },
  };
}

/**
 * Intervallo "min,max" (operatore `between` di Case-When): due numeri,
 * il primo non maggiore del secondo. Il valore resta il testo "min,max" che
 * il backend legge (manipola/transforms/filtering: split(',') + float).
 */
export function numberPair() {
  const num = decimal();
  return {
    kind: 'numberPair',
    format: (v) => (v === null || v === undefined ? '' : String(v)),
    parse(raw) {
      const parts = String(raw ?? '').split(',').map(s => s.trim());
      if (parts.length === 1 && parts[0] === '') return fail(REQUIRED);
      if (parts.length !== 2) return fail('Scrivi due numeri separati da virgola: min,max');
      const [a, b] = parts.map(p => num.parse(p));
      if (a.error || b.error) return fail('Scrivi due numeri separati da virgola: min,max');
      if (a.value > b.value) return fail('Il minimo è maggiore del massimo');
      return ok(`${a.value},${b.value}`);
    },
  };
}

/** Converte con un codec un valore gia' tipizzato o un testo (helper dei form). */
export const parseWith = (codec, raw) => codec.parse(typeof raw === 'string' ? raw : codec.format(raw));
