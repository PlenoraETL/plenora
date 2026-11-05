/**
 * cron.js — la regola unica dell'espressione cron (Fase 5 T5, R8.13).
 *
 * Stessa regola del backend: lo scheduler accetta un cron solo se ha
 * ESATTAMENTE 5 campi e `croniter.is_valid` lo accetta
 * (backend app/core/scheduler/cron_schedule.py, CronSchedule). Qui la stessa
 * grammatica di croniter per i 5 campi; dove croniter e' piu' largo (per
 * esempio `1-L`) il frontend e' piu' stretto e lo dichiara nella tabella dei
 * casi (`tests/fixtures/cronCases.json`, campo `stricter`). Un test del
 * backend fa girare croniter sugli stessi casi
 * (backend/tests2/shared/test_cron_rule_matches_frontend.py).
 *
 * | campo            | valori                  | speciali (da soli)      |
 * |------------------|-------------------------|-------------------------|
 * | minuto           | 0-59                    |                         |
 * | ora              | 0-23                    |                         |
 * | giorno del mese  | 1-31                    | `L`, `?`, `nW`          |
 * | mese             | 1-12, jan-dec           |                         |
 * | giorno settimana | 0-7 (0 e 7 = domenica), | `?`, `n#k` (k 1-5)      |
 * |                  | sun-sat                 |                         |
 *
 * Ogni campo: elenco separato da virgole di `*`, `v` o `a-b`, con `/passo`
 * facoltativo (passo intero >= 1).
 */

const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

const CRON_FIELDS = Object.freeze([
  { name: 'minuto', min: 0, max: 59 },
  { name: 'ora', min: 0, max: 23 },
  { name: 'giorno del mese', min: 1, max: 31 },
  { name: 'mese', min: 1, max: 12, names: MONTHS, namesFrom: 1 },
  { name: 'giorno della settimana', min: 0, max: 7, names: DAYS, namesFrom: 0 },
]);

function valueOf(token, field) {
  if (/^\d+$/.test(token)) {
    const n = Number(token);
    return n >= field.min && n <= field.max ? n : null;
  }
  if (field.names) {
    const i = field.names.indexOf(token.toLowerCase());
    if (i !== -1) return i + field.namesFrom;
  }
  return null;
}

function itemValid(item, field) {
  const [base, step, extra] = item.split('/');
  if (extra !== undefined) return false;
  if (step !== undefined && !(/^\d+$/.test(step) && Number(step) >= 1)) return false;
  if (base === '*') return true;
  const bounds = base.split('-');
  if (bounds.length > 2) return false;
  return bounds.every(b => b !== '' && valueOf(b, field) !== null);
}

function fieldValid(text, index) {
  const field = CRON_FIELDS[index];
  if (index === 2 && (text === 'L' || text === '?')) return true;
  if (index === 2 && /^\d+W$/i.test(text)) return valueOf(text.slice(0, -1), field) !== null;
  if (index === 4 && text === '?') return true;
  if (index === 4 && text.includes('#')) {
    const [day, nth] = text.split('#');
    return valueOf(day, field) !== null && /^[1-5]$/.test(nth || '');
  }
  const items = text.split(',');
  return items.every(item => item !== '' && itemValid(item, field));
}

/** Problema dell'espressione cron, o null se lo scheduler la accetta. */
export function cronProblem(expression) {
  const text = String(expression ?? '').trim();
  if (!text) return 'Espressione cron richiesta';
  const parts = text.split(/\s+/);
  if (parts.length !== 5) {
    return `Servono 5 campi (minuto ora giorno mese giorno_settimana), ne hai scritti ${parts.length}`;
  }
  const bad = parts.findIndex((part, i) => !fieldValid(part, i));
  if (bad !== -1) return `Campo «${CRON_FIELDS[bad].name}» non valido: ${parts[bad]}`;
  return null;
}


/** Forma normalizzata (spazi singoli), quella che si salva. */
export const normalizeCron = (expression) => String(expression ?? '').trim().split(/\s+/).join(' ');
