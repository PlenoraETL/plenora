/**
 * T3 (R5.8, N5) — una sola normalizzazione degli errori verso l'utente.
 *
 * 1. `humanizeError` / `requestErrorMessage` (utils/errorMessages): messaggio
 *    del backend se c'e', altrimenti testo curato del codice, altrimenti un
 *    testo italiano per famiglia (rete, timeout, 401, 403, 404, 409, 422, 5xx).
 * 2. Nessun file di src mostra il testo grezzo di un'eccezione
 *    (`err.message`, `e?.message`, `error.message`, `x.reason.message`) ne'
 *    legge da se' `response.data.message`: passa da errorMessages.
 * 3. Nessun catch mostra un errore solo con un testo fisso, buttando via il
 *    motivo (N5: "Errore salvataggio" al posto di "nome duplicato").
 * Eccezioni dichiarate qui sotto, ciascuna con il motivo.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { humanizeError, requestErrorMessage, backendMessage } from '../../src/utils/errorMessages';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../../src');

const ALLOWED_RAW = [
  { file: 'api/http.js', match: 'error.message = message', motivo: "l'interceptor scrive in err.message il testo gia' normalizzato" },
  { file: 'utils/errorMessages.js', match: 'err.message', motivo: "e' la normalizzazione: legge il testo di un errore locale dell'app" },
  { file: 'components/common/ErrorBoundary.jsx', match: 'error.message', motivo: "dettaglio tecnico mostrato solo nella build di sviluppo (FRONTEND_ENV.isDev)" },
];

const ALLOWED_MUTE = [
  { file: 'components/arricchisci/OpenAPIImportModal.jsx', match: "setError('File non valido", motivo: "errore di lettura del file scelto dall'utente (JSON.parse/schema): il testo fisso e' il motivo esatto" },
];

function listSources(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...listSources(p));
    else if (/\.(jsx?|tsx?)$/.test(name)) out.push(p);
  }
  return out;
}

// Toglie commenti e argomenti di console.*() (i log non sono interfaccia),
// mantenendo le righe al loro posto.
function codeOnly(src) {
  let s = src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');
  const re = /console\.\w+\(/g;
  let m;
  while ((m = re.exec(s))) {
    let depth = 1;
    let j = re.lastIndex;
    while (j < s.length && depth) {
      if (s[j] === '(') depth += 1;
      else if (s[j] === ')') depth -= 1;
      j += 1;
    }
    s = s.slice(0, re.lastIndex) + s.slice(re.lastIndex, j - 1).replace(/[^\n]/g, ' ') + s.slice(j - 1);
  }
  return s;
}

const RAW_MESSAGE = /\b(?:e|err|error|ex|exc|_e|_err|reason|\w+Err|\w+Error)\??\.message\b|response\??\.data\??\.message\b/;
const LITERAL_ERROR_UI = /(?:toast\.error|toast\.warning|set\w*Error|setProblem)\(\s*['"`]/;

const sources = listSources(SRC).map((abs) => {
  const file = relative(SRC, abs).replace(/\\/g, '/');
  return { file, code: codeOnly(readFileSync(abs, 'utf8')) };
});

function rawMessageHits() {
  const hits = [];
  for (const { file, code } of sources) {
    code.split('\n').forEach((line, i) => {
      if (RAW_MESSAGE.test(line)) hits.push({ file, line: i + 1, text: line.trim() });
    });
  }
  return hits;
}

function muteCatchHits() {
  const hits = [];
  for (const { file, code } of sources) {
    const re = /catch\s*(?:\(\s*([A-Za-z_$][\w$]*)\s*\))?\s*\{/g;
    let m;
    while ((m = re.exec(code))) {
      let depth = 1;
      let j = re.lastIndex;
      while (j < code.length && depth) {
        if (code[j] === '{') depth += 1;
        else if (code[j] === '}') depth -= 1;
        j += 1;
      }
      const body = code.slice(re.lastIndex, j - 1);
      const usesError = m[1] ? new RegExp(`\\b${m[1].replace(/\$/g, '\\$')}\\b`).test(body) : false;
      if (!usesError && LITERAL_ERROR_UI.test(body)) {
        hits.push({ file, line: code.slice(0, m.index).split('\n').length, text: body.trim().replace(/\s+/g, ' ').slice(0, 120) });
      }
    }
    // Stessa regola per le promise: .catch(() => ...) / .catch((x) => ...).
    const pr = /\.catch\(\s*(?:\(\s*([A-Za-z_$][\w$]*)?\s*\)|([A-Za-z_$][\w$]*))\s*=>/g;
    while ((m = pr.exec(code))) {
      let depth = 1;
      let j = code.indexOf('(', m.index) + 1;
      while (j < code.length && depth) {
        if (code[j] === '(') depth += 1;
        else if (code[j] === ')') depth -= 1;
        j += 1;
      }
      const body = code.slice(pr.lastIndex, j - 1);
      const name = m[1] || m[2];
      const usesError = name ? new RegExp(`\\b${name.replace(/\$/g, '\\$')}\\b`).test(body) : false;
      if (!usesError && LITERAL_ERROR_UI.test(body)) {
        hits.push({ file, line: code.slice(0, m.index).split('\n').length, text: body.trim().replace(/\s+/g, ' ').slice(0, 120) });
      }
    }
  }
  return hits;
}

const allowed = (list, hit) => list.some((a) => a.file === hit.file && hit.text.includes(a.match));

describe('una sola normalizzazione degli errori (T3)', () => {
  it('nessun testo grezzo di eccezione in toast, banner e setError', () => {
    const offenders = rawMessageHits().filter((h) => !allowed(ALLOWED_RAW, h));
    expect(offenders.map((h) => `${h.file}:${h.line} ${h.text}`)).toEqual([]);
  });

  it('nessun catch che mostra solo un testo fisso buttando via il motivo (N5)', () => {
    const offenders = muteCatchHits().filter((h) => !allowed(ALLOWED_MUTE, h));
    expect(offenders.map((h) => `${h.file}:${h.line} ${h.text}`)).toEqual([]);
  });

  it('ogni eccezione dichiarata ha un motivo ed e\' ancora in uso', () => {
    const raw = rawMessageHits();
    const mute = muteCatchHits();
    for (const a of ALLOWED_RAW) {
      expect(a.motivo.length, a.file).toBeGreaterThan(10);
      expect(raw.some((h) => h.file === a.file && h.text.includes(a.match)), `${a.file}: ${a.match}`).toBe(true);
    }
    for (const a of ALLOWED_MUTE) {
      expect(a.motivo.length, a.file).toBeGreaterThan(10);
      expect(mute.some((h) => h.file === a.file && h.text.includes(a.match)), `${a.file}: ${a.match}`).toBe(true);
    }
  });

  it('la guardia riconosce le forme vietate', () => {
    for (const bad of [
      "toast.error(e.message)",
      "setError(err?.message || 'x')",
      'setX(`a ${error.message}`)',
      'const m = r.reason?.message',
      "const m = e?.response?.data?.message || 'x'",
    ]) {
      expect(RAW_MESSAGE.test(bad), bad).toBe(true);
    }
    expect(codeOnly("console.warn('x',\n  err.message,\n);").includes('err.message')).toBe(false);
  });
});

describe('humanizeError / requestErrorMessage: regola unica', () => {
  const http = (status, data) => ({ message: `Request failed with status code ${status}`, response: { status, data } });

  it('messaggio del backend prima di tutto (anche con codice mappato)', () => {
    expect(requestErrorMessage(http(409, { error: 'connection_duplicate_name', message: 'Nome "x" gia\' usato' }))).toBe('Nome "x" gia\' usato');
    expect(requestErrorMessage(http(400, { detail: 'Legacy' }))).toBe('Legacy');
    expect(requestErrorMessage(http(409, { detail: { message: 'Annidato' } }))).toBe('Annidato');
    expect(requestErrorMessage(http(422, { detail: [{ loc: ['body', 'name'], msg: 'obbligatorio' }, { msg: 'altro' }] })))
      .toBe('body.name: obbligatorio; altro');
  });

  it('senza messaggio del backend: testo curato del codice', () => {
    expect(requestErrorMessage(http(409, { error: 'connection_in_use' }))).toMatch(/ancora usata/);
  });

  it.each([
    [401, /accesso/],
    [403, /permessi/],
    [404, /non esiste/],
    [409, /conflitto/],
    [422, /non sono validi/],
    [500, /errore interno/],
    [503, /errore interno/],
  ])('famiglia %s: testo italiano', (status, re) => {
    expect(requestErrorMessage(http(status, ''))).toMatch(re);
  });

  it('rete, timeout, annullata: mai il testo di axios', () => {
    expect(requestErrorMessage({ message: 'Network Error', code: 'ERR_NETWORK', isAxiosError: true })).toMatch(/Impossibile contattare il server/);
    expect(requestErrorMessage({ message: 'timeout of 30000ms exceeded', code: 'ECONNABORTED', isAxiosError: true })).toMatch(/non ha risposto in tempo/);
    expect(requestErrorMessage({ message: 'canceled', code: 'ERR_CANCELED', name: 'CanceledError' })).toMatch(/annullata/);
  });

  it("errore locale dell'app: il suo testo; errore di programma: mai", () => {
    expect(requestErrorMessage(new Error('cURL senza URL'))).toBe('cURL senza URL');
    expect(requestErrorMessage(new TypeError("Cannot read properties of undefined (reading 'x')"))).not.toMatch(/Cannot read/);
    expect(requestErrorMessage(new TypeError('x'), 'cURL non valido')).toBe('cURL non valido');
    expect(requestErrorMessage(null, 'Fallback')).toBe('Fallback');
  });

  it('il titolo segue il codice o la famiglia', () => {
    expect(humanizeError(http(403, { error: 'auth_role_admin_required', message: 'Serve admin' }))).toEqual({ title: 'Permessi insufficienti', message: 'Serve admin' });
    expect(humanizeError(http(500, {})).title).toBe('Errore del server');
  });

  it('backendMessage ignora corpi senza testo', () => {
    expect(backendMessage(null)).toBeNull();
    expect(backendMessage({ message: '  ' })).toBeNull();
    expect(backendMessage({ detail: [] })).toBeNull();
  });
});
