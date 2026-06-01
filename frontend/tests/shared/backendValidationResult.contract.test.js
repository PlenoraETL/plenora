/**
 * Contract — backendReportToResult / validationRequestFailedResult.
 * Mappa la risposta di POST /validate nel CanvasValidationResult consumato dal FE.
 */
import { describe, it, expect } from 'vitest';
import {
  backendReportToResult,
  validationRequestFailedResult,
} from '../../src/utils/backendValidationResult';

describe('backendReportToResult', () => {
  it('mappa code→id, node_id→nodeId, source=backend', () => {
    const result = backendReportToResult({
      valid: false,
      has_errors: true,
      issues: [
        { node_id: 'src-1', severity: 'error', code: 'SOURCE_ISOLATED', message: 'isolato' },
        { node_id: '', severity: 'warning', code: 'COLUMN_NAME_CONFLICT', message: 'conflitto' },
      ],
    });
    expect(result.ok).toBe(false);
    expect(result.issues).toHaveLength(2);
    expect(result.issues[0]).toMatchObject({
      id: 'SOURCE_ISOLATED', severity: 'error', message: 'isolato', nodeId: 'src-1', source: 'backend',
    });
    // node_id vuoto → nodeId omesso (non '')
    expect(result.issues[1].nodeId).toBeUndefined();
    expect(result.bySeverity.error).toHaveLength(1);
    expect(result.bySeverity.warning).toHaveLength(1);
  });

  it('nessun errore → ok=true', () => {
    const result = backendReportToResult({ valid: true, has_errors: false, issues: [] });
    expect(result.ok).toBe(true);
    expect(result.isValid).toBe(true);
    expect(result.issues).toEqual([]);
  });

  it('issues mancante/non-array → lista vuota', () => {
    expect(backendReportToResult({}).issues).toEqual([]);
    expect(backendReportToResult(null).issues).toEqual([]);
  });
});

describe('validationRequestFailedResult', () => {
  it('produce un solo issue error (no finto ok)', () => {
    const result = validationRequestFailedResult();
    expect(result.ok).toBe(false);
    expect(result.issues).toHaveLength(1);
    expect(result.issues[0].severity).toBe('error');
    expect(result.issues[0].source).toBe('backend');
  });
});
