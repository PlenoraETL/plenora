import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(
  resolve(process.cwd(), 'src/components/nodes/ProjectInputNode.jsx'),
  'utf8',
);

describe('ProjectInputNode visual binding contract', () => {
  it('derives every destination from parameter-binding edges', () => {
    expect(source).toContain('isParameterBindingEdge(edge) && edge.source === id');
    expect(source).toContain('bindingEdges.map(edge =>');
    expect(source).toContain('Destinazione');
    expect(source).not.toContain('ID nodo destinazione');
  });

  it('uses the shared capability resolver instead of hardcoded fields', () => {
    expect(source).toContain('getNodeBindableTargets(targetNode, registry)');
    expect(source).toContain('Parametro configurabile');
    expect(source).not.toContain('bindingOptionsForNode');
  });

  it('exposes advanced validation and multi-file authoring controls', () => {
    expect(source).toContain("Consenti piu' file");
    expect(source).toContain('max_size_bytes');
    expect(source).toContain('min_length');
    expect(source).toContain('max_length');
    expect(source).toContain('minimum');
    expect(source).toContain('maximum');
    expect(source).toContain('pattern');
    // Fase 5 T5: le opzioni sono un elenco con buffer locale (FieldInput).
    expect(source).toContain('codec={OPTIONS}');
  });
});
