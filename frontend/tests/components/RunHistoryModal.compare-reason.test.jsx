/**
 * Isolated contract tests for `mapCompareErrorReason`.
 *
 * Backend `intermediate/router.py:475-505` ritorna 422 con
 * `detail.reason` per due casi noti di diff L2 non disponibile. Il
 * mapper FE produce un messaggio user-facing differenziato per ogni
 * reason, fallback generico altrimenti.
 *
 * Pattern: function pura esportata, niente render integration. Il
 * RunHistoryModal e' troppo grande per render-test economico; il
 * mapper isolato copre il contratto della UX message.
 */

import { describe, it, expect } from 'vitest';

import { mapCompareErrorReason } from '../../src/components/modals/RunHistoryModal';

describe('mapCompareErrorReason', () => {
  it('reason="metadata_only_no_rows" -> messaggio specifico (default)', () => {
    const msg = mapCompareErrorReason('metadata_only_no_rows', null);
    expect(msg).toMatch(/solo i metadati/);
    expect(msg).toMatch(/Confronto riga per riga/i);
    expect(msg).not.toMatch(/^Errore durante il confronto$/);
  });

  it('reason="persistence_mode_mismatch" -> messaggio specifico (default)', () => {
    const msg = mapCompareErrorReason('persistence_mode_mismatch', null);
    expect(msg).toMatch(/in modi diversi/);
    expect(msg).toMatch(/stessa modalità/i);
    expect(msg).not.toMatch(/^Errore durante il confronto$/);
  });

  it('preferisce il `message` del backend quando presente', () => {
    const beMsg = 'Messaggio custom dal backend con dettagli specifici';
    expect(mapCompareErrorReason('metadata_only_no_rows', beMsg)).toBe(beMsg);
    expect(mapCompareErrorReason('persistence_mode_mismatch', beMsg)).toBe(beMsg);
    expect(mapCompareErrorReason('any_unknown_reason', beMsg)).toBe(beMsg);
  });

  it('reason sconosciuto / null / undefined -> fallback generico', () => {
    expect(mapCompareErrorReason(null, null)).toBe('Errore durante il confronto');
    expect(mapCompareErrorReason(undefined, null)).toBe('Errore durante il confronto');
    expect(mapCompareErrorReason('boh', null)).toBe('Errore durante il confronto');
    expect(mapCompareErrorReason('', null)).toBe('Errore durante il confronto');
  });

  it('beMessage stringa vuota e\' trattato come assente -> default per reason', () => {
    const msg = mapCompareErrorReason('persistence_mode_mismatch', '');
    expect(msg).toMatch(/in modi diversi/);
  });

  it('beMessage non stringa (es. obj) e\' ignorato', () => {
    const msg = mapCompareErrorReason('metadata_only_no_rows', { foo: 'bar' });
    expect(msg).toMatch(/solo i metadati/);
  });
});
