import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(
  resolve(process.cwd(), 'src/components/nodes/ProjectInputNode.jsx'),
  'utf8',
);

describe('ProjectInputNode visual binding contract', () => {
  it('derives the destination from canvas edges', () => {
    expect(source).toContain('edges.filter(edge => edge.source === id)');
    expect(source).toContain('Destinazione:');
    expect(source).not.toContain('ID nodo destinazione');
  });

  it('offers only supported runtime-configurable fields', () => {
    expect(source).toContain('filters.${index}.value');
    expect(source).toContain('conditions.${index}.value');
    expect(source).toContain('having_conditions.${index}.value');
    expect(source).toContain('static_parameters.${name}');
  });

  it('exposes advanced validation and multi-file authoring controls', () => {
    expect(source).toContain("Consenti piu' file");
    expect(source).toContain('max_size_bytes');
    expect(source).toContain('min_length');
    expect(source).toContain('max_length');
    expect(source).toContain('minimum');
    expect(source).toContain('maximum');
    expect(source).toContain('pattern');
    expect(source).toContain('options: event.target.value.split');
  });
});
