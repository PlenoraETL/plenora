/**
 * canvasTypes.contract.test.js — Lock dei valori del mirror BE↔FE.
 *
 * Single source of truth FE: `frontend/src/constants/canvasTypes.js`.
 * Sibling BE: `backend/app/shared/canvas_types.py` (VALID_X tuples).
 *
 * Questo test cattura:
 *  1. drift accidentale dei VALORI canonici (rinomina, rimozione)
 *  2. divergenza tra i `value` dei dropdown UI nei sub-component
 *     carica/* e i VALORI canonici di canvasTypes
 *
 * Al cambio dei valori canonici (additive o breaking), bumpare ENTRAMBI
 * i test (qui + `backend/tests2/shared/test_canvas_types_contract.py`)
 * nello stesso commit.
 */

import { describe, it, expect } from 'vitest';
import * as ct from '../../src/constants/canvasTypes';
import { WRITE_MODES as DB_WRITE_MODES } from '../../src/components/nodes/carica/DbDestinationSection';

describe('canvasTypes lockin', () => {
  it('NODE_TYPES contiene esattamente i 14 valori del canvas', () => {
    expect([...ct.NODE_TYPES].sort()).toEqual([
      'api_enrich', 'carica', 'carica_arcgis', 'comment',
      'datasource_node', 'feature_source', 'file_source', 'imdf_publisher',
      'multi_join', 'project_source', 'result', 'source',
      'staticValues', 'transform',
    ]);
  });

  it('COLUMN_TYPES_BASE contiene i 6 tipi base', () => {
    expect([...ct.COLUMN_TYPES_BASE].sort()).toEqual([
      'bool', 'date', 'geometry', 'int', 'real', 'str',
    ]);
  });

  it('WRITE_MODES contiene i 6 modi canvas', () => {
    expect([...ct.WRITE_MODES].sort()).toEqual([
      'append', 'create', 'replace', 'truncate_insert', 'update', 'upsert',
    ]);
  });

  it('DEST_TYPES contiene i 3 canonici (db/arcgis/rest)', () => {
    expect([...ct.DEST_TYPES].sort()).toEqual(['arcgis', 'db', 'rest']);
  });

  it('DEST_TYPES_CARICAN_UI subset 2-way del selettore CaricanNode', () => {
    // `arcgis` non e' qui perche` ha il suo componente dedicato
    // (CaricanArcGISNode + factory caricaArcgis in canvasSerializer.js).
    expect([...ct.DEST_TYPES_CARICAN_UI].sort()).toEqual(['db', 'rest']);
  });

  it('TRANSACTION_MODES contiene 2 valori', () => {
    expect([...ct.TRANSACTION_MODES].sort()).toEqual(['all_or_nothing', 'per_batch']);
  });

  it('CONFLICT_POLICIES canonical FE = abort/skip/force', () => {
    // DRIFT NOTO: il canvas Pydantic BE accetta "update" invece di "force".
    // Fix tracked separato.
    expect([...ct.CONFLICT_POLICIES].sort()).toEqual(['abort', 'force', 'skip']);
  });

  it('REST_METHODS allineato BE↔UI (POST/PUT/PATCH, DELETE rimosso)', () => {
    expect([...ct.REST_METHODS].sort()).toEqual(['PATCH', 'POST', 'PUT']);
  });

  it('REST_BATCH_MODES contiene 2 valori', () => {
    expect([...ct.REST_BATCH_MODES].sort()).toEqual(['batch', 'per_record']);
  });

  it('MULTI_JOIN_TYPES contiene 3 valori', () => {
    expect([...ct.MULTI_JOIN_TYPES].sort()).toEqual(['inner', 'left', 'outer']);
  });

  it('costanti sono frozen (immutabili a runtime)', () => {
    expect(Object.isFrozen(ct.NODE_TYPES)).toBe(true);
    expect(Object.isFrozen(ct.WRITE_MODES)).toBe(true);
    expect(Object.isFrozen(ct.CONFLICT_POLICIES)).toBe(true);
    expect(Object.isFrozen(ct.REST_METHODS)).toBe(true);
  });
});

describe('canvasTypes ↔ UI dropdown alignment', () => {
  it('DbDestinationSection.WRITE_MODES.value ⊆ canvasTypes.WRITE_MODES', () => {
    const uiValues = DB_WRITE_MODES.map(m => m.value);
    const expected = ct.WRITE_MODES;
    // Ogni `value` UI deve essere un valore canonico (no drift label).
    uiValues.forEach(v => expect(expected).toContain(v));
    // E inversamente: ogni valore canonico deve essere offerto dalla UI
    // (CaricanNode espone tutti e 6 i write mode — il subset CRON e' in
    // ScheduleWizard, testato separato se serve).
    expected.forEach(v => expect(uiValues).toContain(v));
  });
});
