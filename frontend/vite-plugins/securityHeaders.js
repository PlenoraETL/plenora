/**
 * Intestazioni di sicurezza nei server di Vite (P31).
 *
 * La definizione e' UNA, nel backend (app/core/security/frontend_headers.py);
 * qui si legge la sua uscita con i DEFAULTS, `security-headers.nginx.conf`
 * (la stessa che nginx include nell'immagine Docker; un test backend la
 * confronta col modulo).
 *
 * - `vite preview` (build servita in locale): le intestazioni di produzione,
 *   identiche, CSP bloccante.
 * - `vite` (sviluppo): CSP in Report-Only (segnala in console senza bloccare)
 *   con le sole aggiunte che il server di sviluppo richiede, dichiarate:
 *   l'hash degli script inline che Vite inietta (preambolo di React Refresh),
 *   'unsafe-inline' negli stili (in sviluppo il CSS arriva come <style>),
 *   ws:/wss: in connect-src (HMR).
 *
 * In entrambi connect-src ammette anche l'origine di VITE_API_URL quando e'
 * assoluto (.env.development: http://localhost:5004), la stessa regola che
 * in produzione applica `--api-url` del modulo backend.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

export const HEADERS_FILE = fileURLToPath(new URL('../security-headers.nginx.conf', import.meta.url));

const CSP = 'Content-Security-Policy';
const CSP_REPORT_ONLY = 'Content-Security-Policy-Report-Only';

/** `add_header Nome "valore" always;` -> { Nome: valore }, nell'ordine del file. */
export function parseNginxHeaders(text) {
  const headers = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const m = /^add_header\s+([A-Za-z0-9-]+)\s+"([^"]*)"\s+always;$/.exec(line);
    if (!m) throw new Error(`security-headers: riga non riconosciuta: ${line}`);
    headers[m[1]] = m[2];
  }
  if (!headers[CSP] && !headers[CSP_REPORT_ONLY]) throw new Error('security-headers: manca la CSP');
  return headers;
}

export function readSecurityHeaders(file = HEADERS_FILE) {
  return parseNginxHeaders(fs.readFileSync(file, 'utf-8'));
}

function parseCsp(value) {
  return value.split(';').map((d) => d.trim()).filter(Boolean).map((d) => d.split(/\s+/));
}

/** Hash CSP degli script inline (senza src) di una pagina HTML. */
export function inlineScriptHashes(html) {
  const hashes = [];
  for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (/\bsrc\s*=/.test(m[1]) || !m[2]) continue;
    hashes.push(`'sha256-${crypto.createHash('sha256').update(m[2], 'utf8').digest('base64')}'`);
  }
  return hashes;
}

/** Origine di un URL assoluto http(s); null se vuoto o relativo (same-origin). */
export function apiOrigin(apiUrl) {
  const value = (apiUrl || '').trim();
  if (!/^https?:\/\//i.test(value)) return null;
  return new URL(value).origin;
}

function extendCsp(value, additions) {
  return parseCsp(value)
    .map(([name, ...sources]) => [name, ...new Set([...sources, ...(additions[name] || [])])].join(' '))
    .join('; ');
}

/** Le intestazioni di produzione con l'API su un altro origine (se c'e'). */
export function previewSecurityHeaders(headers, apiUrl = '') {
  const origin = apiOrigin(apiUrl);
  if (!origin) return headers;
  const name = headers[CSP] ? CSP : CSP_REPORT_ONLY;
  return { ...headers, [name]: extendCsp(headers[name], { 'connect-src': [origin] }) };
}

/** Le intestazioni di produzione adattate al server di sviluppo (vedi sopra). */
export function devSecurityHeaders(headers, scriptHashes = [], apiUrl = '') {
  const { [CSP]: enforced, [CSP_REPORT_ONLY]: reportOnly, ...rest } = headers;
  const origin = apiOrigin(apiUrl);
  const csp = extendCsp(enforced || reportOnly, {
    'script-src': scriptHashes,
    'style-src': ["'unsafe-inline'"],
    'connect-src': [...(origin ? [origin] : []), 'ws:', 'wss:'],
  });
  return { [CSP_REPORT_ONLY]: csp, ...rest };
}

function apply(res, headers) {
  for (const [name, value] of Object.entries(headers)) res.setHeader(name, value);
}

export default function securityHeaders({ file = HEADERS_FILE, apiUrl = '' } = {}) {
  return {
    name: 'plenora-security-headers',
    configureServer(server) {
      const base = readSecurityHeaders(file);
      server.middlewares.use((req, res, next) => {
        // L'HTML di sviluppo e' trasformato da Vite: gli hash si calcolano
        // sul corpo effettivamente inviato.
        const end = res.end;
        res.end = function patchedEnd(chunk, ...rest) {
          if (!res.headersSent) {
            const type = String(res.getHeader('Content-Type') || '');
            const html = chunk && type.includes('text/html') ? String(chunk) : '';
            apply(res, devSecurityHeaders(base, inlineScriptHashes(html), apiUrl));
          }
          return end.call(this, chunk, ...rest);
        };
        next();
      });
    },
    configurePreviewServer(server) {
      const headers = previewSecurityHeaders(readSecurityHeaders(file), apiUrl);
      server.middlewares.use((req, res, next) => {
        apply(res, headers);
        next();
      });
    },
  };
}
