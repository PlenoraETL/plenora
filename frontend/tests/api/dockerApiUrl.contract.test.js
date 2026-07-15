import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '../../..');

describe('Docker frontend/backend API URL contract', () => {
  it('compiles VITE_API_URL into the Vite API client used by axios', () => {
    const runtimeEnv = fs.readFileSync(
      path.join(repoRoot, 'frontend/src/config/runtimeEnv.js'),
      'utf8',
    );
    const config = fs.readFileSync(
      path.join(repoRoot, 'frontend/src/api/config.js'),
      'utf8',
    );
    const http = fs.readFileSync(
      path.join(repoRoot, 'frontend/src/api/http.js'),
      'utf8',
    );

    expect(runtimeEnv).toContain("import.meta.env.VITE_API_URL");
    expect(runtimeEnv).toContain("|| '').trim().replace");
    expect(config).toContain("FRONTEND_ENV.apiBaseUrl");
    expect(config).toContain("export const API_ENDPOINT = `${API_BASE_URL}/api`");
    expect(http).toContain("baseURL: API_ENDPOINT");
  });

  it('passes the compose FRONTEND_VITE_API_URL build arg into the frontend image', () => {
    const dockerfile = fs.readFileSync(
      path.join(repoRoot, 'frontend/Dockerfile'),
      'utf8',
    );
    const compose = fs.readFileSync(
      path.join(repoRoot, 'docker-compose.yml'),
      'utf8',
    );

    expect(dockerfile).toContain('ARG VITE_API_URL=""');
    expect(dockerfile).toContain('ENV VITE_API_URL=${VITE_API_URL}');
    expect(dockerfile).toContain('ARG VITE_SENTRY_TRACES_SAMPLE_RATE="0.1"');
    expect(compose).toContain('VITE_API_URL: ${FRONTEND_VITE_API_URL:-}');
    expect(fs.readFileSync(path.join(repoRoot, 'frontend/nginx.conf'), 'utf8'))
      .toContain('proxy_pass http://backend:5004;');
    expect(compose).toContain('VITE_SENTRY_TRACES_SAMPLE_RATE: ${FRONTEND_VITE_SENTRY_TRACES_SAMPLE_RATE:-0.1}');
  });
});
