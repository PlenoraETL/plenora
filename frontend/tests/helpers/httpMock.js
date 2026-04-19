export function createHttpMock(vi) {
  return {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
}

export function createDownloadBlobMock(vi) {
  return vi.fn();
}
