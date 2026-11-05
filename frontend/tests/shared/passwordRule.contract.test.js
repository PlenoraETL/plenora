import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { PASSWORD_MIN_LENGTH, passwordProblem } from '../../src/utils/passwordRule';

/**
 * Contract R5-N1/N2 (Fase 5 T1): una sola regola della password, uguale al
 * backend. Il numero si legge dal backend (ogni `len(...password) < N`) e
 * nessun file del frontend controlla la lunghezza di una password fuori da
 * utils/passwordRule.js.
 */

const repoRoot = path.resolve(import.meta.dirname, '../../..');
const srcRoot = path.resolve(import.meta.dirname, '../../src');
const BACKEND_RULES = ['backend/app/core/auth/auth.py', 'backend/app/core/auth/users_router.py'];

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

describe('regola della password', () => {
  it('la lunghezza minima e\' quella del backend', () => {
    const limits = BACKEND_RULES.flatMap((file) => {
      const content = fs.readFileSync(path.join(repoRoot, file), 'utf-8');
      return [...content.matchAll(/len\([\w.]*password\)\s*<\s*(\d+)/g)].map((m) => Number(m[1]));
    });
    expect(limits.length).toBeGreaterThanOrEqual(3); // cambio, creazione, reset
    expect(new Set(limits)).toEqual(new Set([PASSWORD_MIN_LENGTH]));
  });

  it('nessun altro file controlla la lunghezza di una password', () => {
    const violations = collectSources(srcRoot)
      .filter((file) => !file.endsWith(`utils${path.sep}passwordRule.js`))
      .filter((file) => /(pwd|password)\w*\.length\s*[<>]=?\s*\d/i.test(fs.readFileSync(file, 'utf-8')))
      .map((file) => path.relative(srcRoot, file));
    expect(violations).toEqual([]);
  });

  it('passwordProblem: vuota, corta, valida', () => {
    expect(passwordProblem('')).toBe('Password obbligatoria');
    expect(passwordProblem('a'.repeat(PASSWORD_MIN_LENGTH - 1))).toMatch(/almeno 12/);
    expect(passwordProblem('a'.repeat(PASSWORD_MIN_LENGTH))).toBeNull();
  });
});

describe('regola della password: stessa misura del backend (len di Python)', () => {
  it('conta i code point, non le unita\' UTF-16', () => {
    // 6 emoji = 12 unita' UTF-16 ma 6 caratteri per Python: corta.
    expect(passwordProblem('😀'.repeat(6))).toMatch(/almeno 12/);
    expect(passwordProblem('😀'.repeat(PASSWORD_MIN_LENGTH))).toBeNull();
    expect(passwordProblem('è'.repeat(PASSWORD_MIN_LENGTH))).toBeNull();
  });

  it('soli spazi: come il backend (nessuna regola speciale)', () => {
    expect(passwordProblem(' '.repeat(PASSWORD_MIN_LENGTH))).toBeNull();
    expect(passwordProblem('   ')).toMatch(/almeno 12/);
    expect(passwordProblem(undefined)).toBe('Password obbligatoria');
  });

  it('il backend non toglie gli spazi prima di misurare', () => {
    const repo = path.resolve(import.meta.dirname, '../../..');
    for (const file of BACKEND_RULES) {
      const content = fs.readFileSync(path.join(repo, file), 'utf-8');
      expect(content).not.toMatch(/password\.strip\(\)/);
    }
  });
});
