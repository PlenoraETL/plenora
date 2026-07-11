import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '../../..');

function source(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

describe('frontend configuration boundary', () => {
  it('keeps all browser env access in runtimeEnv', () => {
    const srcRoot = path.join(repoRoot, 'frontend/src');
    const offenders = [];
    const visit = (directory) => {
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        const absolute = path.join(directory, entry.name);
        if (entry.isDirectory()) visit(absolute);
        else if (/\.(js|jsx|ts|tsx)$/.test(entry.name)
          && absolute !== path.join(srcRoot, 'config/runtimeEnv.js')
          && source(path.relative(repoRoot, absolute)).includes('import.meta.env')) {
          offenders.push(path.relative(srcRoot, absolute));
        }
      }
    };
    visit(srcRoot);
    expect(offenders).toEqual([]);
  });

  it('uses backend-provided essential metadata and current snapshot shapes', () => {
    const page = source('frontend/src/pages/AdminRuntimeConfigPage.jsx');
    expect(page).toContain('item.essential === true');
    expect(page).toContain('data?.runtime?._dev_topology');
    expect(page).toContain('data.workers.running_tasks');
    expect(page).not.toContain('ESSENTIAL_KEYS');
    expect(page).not.toContain('workers_online');
  });

  it('documents every supported VITE variable', () => {
    const runtimeEnv = source('frontend/src/config/runtimeEnv.js');
    const example = source('frontend/.env.example');
    for (const key of runtimeEnv.match(/VITE_[A-Z0-9_]+/g) || []) {
      expect(example).toContain(key);
    }
  });
});
