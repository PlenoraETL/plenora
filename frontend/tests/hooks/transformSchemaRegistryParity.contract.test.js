import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  EXTENDED_SCHEMA_OPS,
  SCHEMA_PASSTHROUGH_OPS,
} from '../../src/utils/extendedTransformSchema';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../../..');

describe('registry backend / propagazione schema frontend', () => {
  it('ogni transform strutturale del registry ha una regola live esplicita', () => {
    const registry = JSON.parse(readFileSync(
      resolve(repoRoot, 'backend/app/core/manipola/transforms_registry.json'),
      'utf8',
    )).transforms;
    const propagationSource = readFileSync(
      resolve(repoRoot, 'frontend/src/hooks/useColumnPropagation.js'),
      'utf8',
    );
    const switchCases = new Set(
      [...propagationSource.matchAll(/case\s+'([^']+)'/g)].map(match => match[1]),
    );
    const handled = new Set([
      ...switchCases,
      ...EXTENDED_SCHEMA_OPS,
      ...SCHEMA_PASSTHROUGH_OPS,
      // Gestiti prima dello switch perche' richiedono gli input separati.
      'join',
      'concat',
    ]);

    const missing = Object.keys(registry)
      .filter(operation => !handled.has(operation));

    expect(missing).toEqual([]);

    const invalidPassthrough = SCHEMA_PASSTHROUGH_OPS.filter(
      operation => registry[operation]?.output?.type !== 'same',
    );
    expect(invalidPassthrough).toEqual([]);
  });
});
