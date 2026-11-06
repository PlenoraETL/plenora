import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, vi } from 'vitest';

import { createHttpMock, createDownloadBlobMock } from './helpers/httpMock';
import {
  createToastMock,
  createAuthContextValue,
  createNavigateMock,
  createSearchParamsMock,
} from './helpers/contextMocks';
import { createReactFlowMock } from './helpers/reactflowMock';
import { resetSharedCache } from '../src/utils/sharedCache';

globalThis.__frontendTestHelpers = {
  createHttpMock,
  createDownloadBlobMock,
  createToastMock,
  createAuthContextValue,
  createNavigateMock,
  createSearchParamsMock,
  createReactFlowMock,
};

globalThis.__frontendConsolePolicy = {
  allowError: false,
};

let consoleErrorSpy;

beforeEach(() => {
  // Cache dati condivisa (UI-1): ogni test parte vuoto, come una sessione nuova.
  resetSharedCache();
  globalThis.__frontendConsolePolicy.allowError = false;
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  const calls = consoleErrorSpy?.mock.calls ?? [];
  consoleErrorSpy?.mockRestore();

  if (!globalThis.__frontendConsolePolicy.allowError && calls.length > 0) {
    const rendered = calls
      .map((args) => args.map((value) => {
        if (value instanceof Error) return value.stack || value.message;
        return typeof value === 'string' ? value : JSON.stringify(value);
      }).join(' '))
      .join('\n');

    throw new Error(`Unexpected console.error during frontend test:\n${rendered}`);
  }
});

// Guardia: i test con `@vitest-environment node` (es. tests/security) non hanno window.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = function matchMedia(query) {
    return {
      matches: false,
      media: query,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    };
  };
}
