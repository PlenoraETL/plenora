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
    // Il tipo "connessione" e il suo nome vengono dal glossario (INPUT_KIND_LABELS).
    expect(source).toContain('INPUT_KIND_LABELS');
    expect(read('src/utils/glossary.js')).toContain("connection: 'Set di connessioni'");
    expect(source).toContain('connection_set_id');
    expect(source).toContain("kind !== 'connection'");
    expect(source).toContain('seleziona la connessione dell’intero progetto Estrai');
  });

  // Fase 5 T2 (BA2): gli Eseguibili usano la finestra unica dell'editor; le
  // opzioni di connessione sono le etichette pubbliche, si invia la chiave.
  it('renders searchable public labels while submitting only selector keys', () => {
    const form = read('src/components/eseguibili/ExecutableDynamicRunForm.jsx');
    expect(form).toContain('input_kind: d.kind');
    const window = read('src/components/editor/BeforeRunModal.jsx');
    expect(window).toContain("definition.input_kind === 'connection'");
    expect(window).toContain('value: o.key');
    expect(window).toContain('label: o.label');
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
