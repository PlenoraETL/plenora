import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = relative => readFileSync(
  resolve(process.cwd(), relative),
  'utf8',
);

describe('Connection Set runtime contract', () => {
  it('authors a project-level connection selector without a node handle', () => {
    const source = read('src/components/nodes/ProjectInputNode.jsx');
    expect(source).toContain("['connection', 'Set di connessioni']");
    expect(source).toContain('connection_set_id');
    expect(source).toContain("kind !== 'connection'");
    expect(source).toContain('seleziona la connessione dell’intero progetto Estrai');
  });

  it('renders searchable public labels while submitting only selector keys', () => {
    const source = read(
      'src/components/eseguibili/ExecutableDynamicRunForm.jsx',
    );
    expect(source).toContain("definition.kind === 'connection'");
    expect(source).toContain('option.key');
    expect(source).toContain('option.label');
  });

  it('binds Estrai filter values through parameter-binding edges', () => {
    const source = read('src/components/modals/FilterModal.jsx');
    expect(source).toContain("{termName('runtime_input')}:");
    expect(source).toContain('createParameterBindingEdge');
    expect(source).toContain('filters.${index}.value');
    expect(source).toContain("Valore fornito all'esecuzione");
  });

  it('keeps connection credentials behind connection ids', () => {
    const source = read(
      'src/components/connections/ConnectionSetsPanel.jsx',
    );
    expect(source).toContain('connection_id');
    expect(source).not.toContain('password');
    expect(source).not.toContain('connection_string');
  });
});
