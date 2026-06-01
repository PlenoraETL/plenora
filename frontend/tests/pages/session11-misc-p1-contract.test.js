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
