/**
 * Sessione 11 misc P1 — useCanvasValidation, ProjectSourceNode, DataSourceManager.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


function readSrc(relPath) {
  return readFileSync(resolve(__dirname, '../../src', relPath), 'utf-8');
}


describe('Sessione 11 misc P1', () => {
  it('useCanvasValidation useEffect deps: no piu nodes.length ridondante', () => {
    const src = readSrc('hooks/useCanvasValidation.js');
    // Trova l'useEffect target (con validateOnChange + debounceMs)
    const idx = src.indexOf('if (!validateOnChange || nodes.length === 0)');
    expect(idx).toBeGreaterThan(-1);
    // I prossimi ~600 char includono il closing }, [...]) dell'useEffect
    const window = src.slice(idx, idx + 1200);
    // Cerca il blocco deps `}, [...]`
    const depsMatch = window.match(/\}\s*,\s*\[([^\]]+)\]\)/);
    expect(depsMatch).toBeTruthy();
    const depsStr = depsMatch[1];
    // Anti-regression: nodes.length NON deve essere nelle deps
    expect(depsStr).not.toMatch(/\bnodes\.length\b/);
    // Le deps essenziali invece restano
    expect(depsStr).toMatch(/nodesFingerprint/);
  });

  it('ProjectSourceNode logga warning aggregato su drift schema', () => {
    const src = readSrc('components/nodes/ProjectSourceNode.jsx');
    expect(src).toContain('_missingLogicalCount');
    expect(src).toMatch(/console\.warn[\s\S]{0,200}Drift schema BE/);
  });

  it('DataSourceManager handleDelete ha guard re-entrancy + disabling button', () => {
    const src = readSrc('pages/DataSourceManager.jsx');
    expect(src).toContain('deletingId');
    expect(src).toContain('setDeletingId');
    // Re-entrancy guard nel handler
    expect(src).toMatch(/if\s*\(deletingId\s*===\s*id\)\s*return/);
    // Bottone Elimina con disabled state
    expect(src).toMatch(/disabled=\{deletingId\s*===\s*confirmDelete\.id\}/);
    // Setting back to null nel finally
    expect(src).toMatch(/setDeletingId\(null\)/);
  });
});
