import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const testsRoot = path.resolve(import.meta.dirname, '..');
const selfPath = path.resolve(import.meta.filename);

const bannedPatterns = [
  { label: 'expect(true).toBe(true)', regex: /expect\(\s*true\s*\)\.toBe\(\s*true\s*\)/g },
  { label: 'expect(false).toBe(false)', regex: /expect\(\s*false\s*\)\.toBe\(\s*false\s*\)/g },
  { label: 'expect(1).toBe(1)', regex: /expect\(\s*1\s*\)\.toBe\(\s*1\s*\)/g },
  { label: 'it.skip', regex: /\bit\.skip\s*\(/g },
  { label: 'test.skip', regex: /\btest\.skip\s*\(/g },
  { label: 'describe.skip', regex: /\bdescribe\.skip\s*\(/g },
  { label: 'TODO comment', regex: /\/\/\s*TODO\b/g },
  { label: 'FIXME comment', regex: /\/\/\s*FIXME\b/g },
];

function collectTestFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return collectTestFiles(fullPath);
    }
    if (!entry.isFile() || !entry.name.includes('.test.')) {
      return [];
    }
    return [fullPath];
  });
}

describe('frontend test antipattern contract', () => {
  it('rejects trivial assertions and skipped tests in the frontend suite', () => {
    const violations = [];

    for (const filePath of collectTestFiles(testsRoot)) {
      if (path.resolve(filePath) === selfPath) continue;
      const content = fs.readFileSync(filePath, 'utf-8');
      const relativePath = path.relative(testsRoot, filePath);

      for (const { label, regex } of bannedPatterns) {
        if (regex.test(content)) {
          violations.push(`${relativePath}: ${label}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
