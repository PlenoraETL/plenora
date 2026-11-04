/**
 * Contract test per `useMountedRef`.
 *
 * Helper estratto come single-source del pattern "guard async callback
 * contro setState post-unmount" (vedi useEnrichmentValidation,
 * useProjectListData).
 */

import { renderHook } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { useMountedRef } from '../../src/hooks/useMountedRef';


describe('useMountedRef — mount/unmount lifecycle contract', () => {
  it('returns ref with current=true at mount', () => {
    const { result } = renderHook(() => useMountedRef());
    expect(result.current.current).toBe(true);
  });

  it('sets current=false at unmount', () => {
    const { result, unmount } = renderHook(() => useMountedRef());
    expect(result.current.current).toBe(true);
    unmount();
    expect(result.current.current).toBe(false);
  });

  it('ref identity is stable across renders', () => {
    const { result, rerender } = renderHook(() => useMountedRef());
    const firstRef = result.current;
    rerender();
    expect(result.current).toBe(firstRef);
  });
});
