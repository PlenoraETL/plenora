import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

const manifestPath = resolve(process.cwd(), 'src/api/openapi-contract.manifest.json');

const loadManifest = () => JSON.parse(readFileSync(manifestPath, 'utf8'));

describe('frontend OpenAPI contract manifest', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('locks the backend login request schema used by the frontend client', async () => {
    const manifest = loadManifest();
    const loginEndpoint = manifest.endpoints.find(
      endpoint => endpoint.method === 'POST' && endpoint.path === '/api/auth/login',
    );
    const loginFields = manifest.schemas.LoginRequest.fields;

    expect(loginEndpoint.requestSchema).toBe('LoginRequest');
    expect(loginFields.username.required).toBe(true);
    expect(loginFields.password.required).toBe(true);
    expect(loginFields.mfa_code.required).toBe(false);

    httpMock.post.mockResolvedValue({ data: { access_token: 'jwt-x' } });
    const { login } = await import('../../src/api/auth');

    await login('alice', 'pwd');
    expect(httpMock.post).toHaveBeenLastCalledWith('/auth/login', {
      username: 'alice',
      password: 'pwd',
    });

    await login('alice', 'pwd', '123456');
    expect(httpMock.post).toHaveBeenLastCalledWith('/auth/login', {
      username: 'alice',
      password: 'pwd',
      mfa_code: '123456',
    });
  });
});
