import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(
  resolve(process.cwd(), 'src/components/eseguibili/ExecutableDynamicRunForm.jsx'),
  'utf8',
);

describe('ExecutableDynamicRunForm multi-file contract', () => {
  it('analyzes every selected file and sends an array only for multi-file inputs', () => {
    expect(source).toContain('Array.from(fileList || [])');
    expect(source).toContain('multiple={Boolean(definition.multiple)}');
    expect(source).toContain('analyzeFile(definition, index)');
    expect(source).toContain('definition.multiple ? payloads : payloads[0]');
  });

  it('does not allow execution with selected but unanalyzed files', () => {
    expect(source).toContain('items.every(item => Boolean(item.analysis))');
  });

  it('projects manifest validation constraints into native form controls', () => {
    expect(source).toContain('min: definition.validation?.minimum');
    expect(source).toContain('maxLength: definition.validation?.max_length');
    expect(source).toContain('pattern: definition.validation?.pattern');
  });
});
