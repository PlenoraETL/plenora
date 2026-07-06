import { describe, expect, it } from 'vitest';

import {
  parseCurlCommand,
  tokenizeCurlCommand,
} from '../../src/utils/curlImport';

describe('curlImport parser', () => {
  it('tokenizes quoted multiline curl commands', () => {
    expect(tokenizeCurlCommand("curl \\\n      -H 'X-Test: a b' \"https://api.example.test/items?q=Roma\"")).toEqual([
      'curl',
      '-H',
      'X-Test: a b',
      'https://api.example.test/items?q=Roma',
    ]);
  });

  it('imports GET query params as fixed parameters and strips the URL query', () => {
    const parsed = parseCurlCommand(
      "curl -L 'https://api.example.test/search?q=Roma&page=1' -H 'Accept: application/json'",
    );

    expect(parsed.url).toBe('https://api.example.test/search');
    expect(parsed.method).toBe('GET');
    expect(parsed.static_parameters).toEqual({ q: 'Roma', page: '1' });
    expect(parsed.parameters).toEqual([
      { name: 'q', type: 'string', required: false, mode: 'fixed' },
      { name: 'page', type: 'string', required: false, mode: 'fixed' },
    ]);
    expect(parsed.custom_headers).toEqual({ Accept: 'application/json' });
    expect(parsed.request).toEqual({ allow_redirects: true });
  });

  it('imports POST JSON body and bearer auth', () => {
    const parsed = parseCurlCommand(
      "curl -X POST 'https://api.example.test/items?tenant=it' " +
        "-H 'Authorization: Bearer SECRET' " +
        "-H 'Content-Type: application/json' " +
        "--data-raw '{\"city\":\"Roma\",\"limit\":2}'",
    );

    expect(parsed.url).toBe('https://api.example.test/items?tenant=it');
    expect(parsed.method).toBe('POST');
    expect(parsed.auth).toEqual({ type: 'bearer', token: 'SECRET' });
    expect(parsed.custom_headers).toEqual({ 'Content-Type': 'application/json' });
    expect(parsed.static_parameters).toEqual({ city: 'Roma', limit: 2 });
    expect(parsed.request).toEqual({ body_type: 'json' });
  });

  it('imports form-urlencoded body and common API key headers as auth config', () => {
    const parsed = parseCurlCommand(
      "curl 'https://api.example.test/search' " +
        "-H 'X-API-Key: KEY123' " +
        "-H 'Content-Type: application/x-www-form-urlencoded' " +
        "--data 'q=Roma&page=1'",
    );

    expect(parsed.method).toBe('POST');
    expect(parsed.auth).toEqual({
      type: 'api_key',
      location: 'header',
      key_name: 'X-API-Key',
      key_value: 'KEY123',
    });
    expect(parsed.static_parameters).toEqual({ q: 'Roma', page: '1' });
    expect(parsed.request).toEqual({ body_type: 'form-urlencoded' });
  });

  it('imports basic auth from -u', () => {
    const parsed = parseCurlCommand(
      "curl -u 'svc:secret' 'https://api.example.test/private'",
    );

    expect(parsed.auth).toEqual({
      type: 'basic_auth',
      username: 'svc',
      password: 'secret',
    });
  });

  it('does not treat unknown option values as the URL', () => {
    const parsed = parseCurlCommand(
      "curl --retry-delay 5 'https://api.example.test/search?q=Roma'",
    );

    expect(parsed.url).toBe('https://api.example.test/search');
    expect(parsed.static_parameters).toEqual({ q: 'Roma' });
  });

  it('rejects commands without an http(s) URL candidate', () => {
    expect(() => parseCurlCommand('curl --retry-delay 5'))
      .toThrow(/senza URL http\(s\)/);
  });

  it('rejects methods unsupported by the connector', () => {
    expect(() => parseCurlCommand("curl -X HEAD 'https://api.example.test/x'"))
      .toThrow(/Metodo HTTP non supportato/);
  });
});
