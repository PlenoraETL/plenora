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

  // Fase 5 T2 (R9.10): eliminazione per elemento con hooks/useBusy (un
  // secondo clic sullo stesso elemento non parte; la fine di A non riabilita B).
  it('DataSourceManager handleDelete per elemento (useBusy) + bottone spento', () => {
    const src = readSrc('pages/DataSourceManager.jsx');
    expect(src).toContain('useBusy()');
    expect(src).toMatch(/busy\.run\(`delete:\$\{id\}`/);
    expect(src).toMatch(/disabled=\{busy\.isBusy\(`delete:\$\{confirmDelete\.id\}`\)\}/);
    expect(src).not.toContain('setDeletingId');
  });
});
