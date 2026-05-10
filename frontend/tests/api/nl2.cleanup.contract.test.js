import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Contract tests for NL2Pipeline frontend cleanup.
 *
 * Decisione di triage: la feature NL2Pipeline lato backend non
 * esiste piu' (zero endpoint), quindi la superficie frontend e' stale
 * code da rimuovere, non da migrare a un nuovo boundary api/.
 *
 * C3.4 ha rimosso:
 *   - state/refs/handlers/UI/polling NL2 in pages/ProjectList.jsx
 *   - blocco CSS NL2 in styles/ProjectList.css
 *   - stub api/nl2pipelinePresets.js (eliminato)
 *   - voce nl2pipelinePresets.js da CODE_MAP.md
 *   - placeholder comment NL2PIPELINE in pages/UserManager.jsx
 *
 * Questo test cristallizza l'invariante "nessuna superficie NL2 viva
 * in src/": se un futuro PR reintroduce nl2*, il test rompe e
 * costringe a giustificare esplicitamente la riapertura della feature.
 *
 * Eccezioni esplicite (whitelist): le note storiche/JSDoc nei contract
 * test sono ammesse perche' documentano *perche'* il cleanup esiste e
 * non sono codice eseguibile.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(__dirname, '../../src');

function* walkSrc(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      yield* walkSrc(full);
    } else if (/\.(jsx?|css)$/.test(name)) {
      yield full;
    }
  }
}

function findNl2Hits() {
  const hits = [];
  // Pattern case-insensitive: copre nl2, NL2, Nl2, nl2pipeline, nl2Presets,
  // .pl__nl2-*, ecc. Sufficientemente largo per catturare regressioni
  // ovvie senza falsi positivi su parole non correlate.
  const pat = /nl2/i;
  for (const file of walkSrc(SRC_DIR)) {
    const src = readFileSync(file, 'utf-8');
    if (pat.test(src)) hits.push(file);
  }
  return hits;
}

describe('NL2Pipeline cleanup contract (C3.4)', () => {
  it('removes the dead presets stub from src/api/', () => {
    const stubPath = resolve(SRC_DIR, 'api/nl2pipelinePresets.js');
    expect(existsSync(stubPath)).toBe(false);
  });

  it('leaves no nl2-related references in any src/ file (.js/.jsx/.css)', () => {
    const hits = findNl2Hits();
    expect(hits, `nl2 references trovate in:\n${hits.join('\n')}`).toEqual([]);
  });

  describe('ProjectList.jsx surgical cleanup', () => {
    const src = readFileSync(resolve(SRC_DIR, 'pages/ProjectList.jsx'), 'utf-8');

    it('does NOT define any nl2* state or refs', () => {
      expect(/\bnl2\w+/i.test(src)).toBe(false);
    });

    it('does NOT import http (only NL2 used it)', () => {
      expect(/import\s+http\s+from\s+['"]\.\.\/api\/http['"]/.test(src)).toBe(
        false,
      );
    });

    it('does NOT call http.<verb>(...) anywhere', () => {
      expect(/\bhttp\.(get|post|put|delete|patch)\s*\(/.test(src)).toBe(false);
    });

    it('does NOT reference handleNl2Generate or nl2AbortRef', () => {
      expect(/handleNl2Generate|nl2AbortRef|nl2LogsRef/.test(src)).toBe(false);
    });
  });

  describe('ProjectList.css surgical cleanup', () => {
    const src = readFileSync(
      resolve(SRC_DIR, 'styles/ProjectList.css'),
      'utf-8',
    );

    it('does NOT contain .pl__nl2-* selectors', () => {
      expect(/\.pl__nl2-/.test(src)).toBe(false);
    });
  });

  describe('CODE_MAP.md does not list nl2pipelinePresets as active client', () => {
    const codeMap = readFileSync(
      resolve(__dirname, '../../../CODE_MAP.md'),
      'utf-8',
    );
    it('removes nl2pipelinePresets.js from the api/ client list', () => {
      expect(/\bnl2pipelinePresets\.js\b/.test(codeMap)).toBe(false);
    });
  });
});
