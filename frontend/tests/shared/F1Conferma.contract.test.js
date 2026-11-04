import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { PARAM_DESCRIPTIONS, INPUT_KIND_LABELS } from '../../src/utils/glossary';
import { nodeDescriptor } from '../../src/components/canvas/nodeDescriptors';

/** Contract dal giro di conferma F1/G del Blocco A. */
const REGISTRY = path.resolve(__dirname, '../../../backend/app/core/manipola/transforms_registry.json');

describe('giro di conferma F1/G', () => {
  it('ogni descrizione riscritta riguarda un parametro che esiste nel catalogo', () => {
    const registry = JSON.parse(fs.readFileSync(REGISTRY, 'utf-8')).transforms;
    for (const [op, params] of Object.entries(PARAM_DESCRIPTIONS)) {
      const ids = (registry[op]?.parameters || []).map(p => p.id);
      for (const id of Object.keys(params)) expect({ op, id, exists: ids.includes(id) }).toEqual({ op, id, exists: true });
    }
  });

  it('il riepilogo dell\'input runtime usa il nome italiano del tipo', () => {
    for (const [kind, label] of Object.entries(INPUT_KIND_LABELS)) {
      expect(nodeDescriptor('project_input').summary({ input_key: 'k', input_kind: kind })).toBe(`k · ${label}`);
    }
  });

  it('la cancellazione nativa di React Flow e\' spenta: si cancella solo dal nostro gestore o da "Elimina passo"', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../../src/components/editor/EditorShell.jsx'), 'utf-8');
    expect(src).toMatch(/deleteKeyCode=\{null\}/);
  });
});
