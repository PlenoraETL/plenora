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
    expect(source).toContain('column => Boolean(item.mapping?.[column.name])');
  });

  it('projects manifest validation constraints into native form controls', () => {
    expect(source).toContain('min: definition.validation?.minimum');
    expect(source).toContain('maxLength: definition.validation?.max_length');
    expect(source).toContain('pattern: definition.validation?.pattern');
  });

  it('uses the shared modal and distinct configuration, progress and result states', () => {
    expect(source).toContain('<ModalWrapper');
    expect(source).toContain('className="esw-run-modal"');
    expect(source).toContain('useSmoothProgress(taskProgress, isRunning)');
    expect(source).toContain('Elaborazione in corso');
    expect(source).toContain('Il risultato è pronto');
    expect(source).not.toContain('className="esw-overlay"');
  });

  it('locks file-based output to the extension inferred by the backend', () => {
    expect(source).toContain('eseguibile.output_is_file_based && eseguibile.inferred_output_extension');
    expect(source).toContain("? eseguibile.inferred_output_extension");
  });

  it('downloads through the executable-owned endpoint', () => {
    expect(source).toContain('downloadEseguibileOutput(');
    expect(source).toContain('eseguibile.id,');
    expect(source).not.toContain('downloadTaskOutput(');
  });
});
