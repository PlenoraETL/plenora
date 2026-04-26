import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Contract test — ExecutionModal FE/API boundary (C3.4 Phase 4).
 *
 * Pre-C3.4 ExecutionModal.jsx (componente UI in components/modals/) faceva
 *   const { default: http } = await import('../../api/http');
 *   const r = await http.get(url, { responseType: 'blob' });
 * dentro l'onClick del bottone "Scarica Report Errori". Doppia
 * violazione del contratto "frontend/src/api/ unico punto HTTP":
 *   1. dynamic import('../../api/http') dentro un componente UI
 *      (oltretutto inutile: ExecutionModal e' sempre montato dietro
 *      `isOpen`, niente lazy split-chunk reale).
 *   2. http.get diretto su un URL costruito con API_ENDPOINT a mano,
 *      bypassando i client per-modulo di api/.
 *
 * C3.4 fix: il download passa per `api/projects.downloadProjectFile`,
 * il dynamic import sparisce, l'import di API_ENDPOINT non serve piu'.
 *
 * Test source-level (regex su sorgente JSX letta via node:fs): niente
 * render React, nessuna dipendenza da jsdom.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE_PATH = resolve(
  __dirname,
  '../../src/components/modals/ExecutionModal.jsx',
);
const SOURCE = readFileSync(SOURCE_PATH, 'utf-8');

describe('ExecutionModal boundary contract (C3.4)', () => {
  it('does NOT dynamic-import api/http inside any handler', () => {
    // Regression: pre-C3.4 era
    //   const { default: http } = await import('../../api/http');
    // Cattura sia path relativi (../../api/http) sia varianti col solo
    // suffisso. Non matcha la stringa 'api/http' dentro commenti che
    // NON contengono un'invocazione `import(...)` come token attivo.
    const dynamicImport = /\bimport\s*\(\s*['"][^'"]*\/api\/http['"]\s*\)/;
    // Filtra le occorrenze che vivono SOLO dentro commenti di una riga.
    // Heuristica: il match e' violazione solo se sulla stessa riga NON
    // c'e' '//' che lo precede.
    const isLiveViolation = SOURCE.split('\n').some((line) => {
      if (!dynamicImport.test(line)) return false;
      const idx = line.indexOf('import(');
      const commentIdx = line.indexOf('//');
      // import( prima del commento, oppure non c'e' commento -> live.
      return commentIdx === -1 || idx < commentIdx;
    });
    expect(isLiveViolation).toBe(false);
  });

  it('does NOT statically import http from api/http', () => {
    expect(/import\s+http\s+from\s+['"][^'"]*\/api\/http['"]/.test(SOURCE)).toBe(
      false,
    );
  });

  it('does NOT call http.<verb>(...) anywhere', () => {
    expect(/\bhttp\.(get|post|put|delete|patch)\s*\(/.test(SOURCE)).toBe(false);
  });

  it('does NOT import API_ENDPOINT (no manual URL building)', () => {
    // L'unico consumer di API_ENDPOINT in questo file era
    // getErrorReportUrl(): URL costruita a mano con concatenazione
    // template. Post-C3.4 il boundary api/projects gestisce tutto.
    expect(/\bAPI_ENDPOINT\b/.test(SOURCE)).toBe(false);
  });

  it('imports downloadProjectFile from api/projects', () => {
    const pattern = /import\s*\{[^}]*\bdownloadProjectFile\b[^}]*\}\s*from\s*['"][^'"]*\/api\/projects['"]/;
    expect(pattern.test(SOURCE)).toBe(true);
  });

  it('actually invokes downloadProjectFile in an error-report handler', () => {
    expect(/\bdownloadProjectFile\s*\(/.test(SOURCE)).toBe(true);
  });

  it('preserves the error_report.csv download filename', () => {
    // Behavior preservation: il nome del file scaricato resta
    // bit-per-bit `error_report.csv`.
    expect(/['"]error_report\.csv['"]/.test(SOURCE)).toBe(true);
  });
});
