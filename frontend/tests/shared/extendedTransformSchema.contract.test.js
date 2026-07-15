import { describe, expect, it } from 'vitest';

import {
  applyExtendedTransformSchema,
  EXTENDED_SCHEMA_OPS,
} from '../../src/utils/extendedTransformSchema';

const column = (name, type = 'str') => ({ name, type });

describe('applyExtendedTransformSchema', () => {
  it('ogni operazione dichiarata ha un ramo implementato', () => {
    const generic = [column('id', 'int'), column('geometry', 'geometry:point')];
    EXTENDED_SCHEMA_OPS.forEach(operation => {
      expect(
        applyExtendedTransformSchema(operation, {}, generic, [generic, generic]),
        operation,
      ).not.toBeNull();
    });
  });

  it('propaga colonne configurabili create dai transform base', () => {
    const input = [column('id', 'int'), column('testo')];

    expect(applyExtendedTransformSchema(
      'split_column',
      { new_columns: ['nome', 'cognome'] },
      input,
    ).map(col => col.name)).toEqual(['id', 'testo', 'nome', 'cognome']);

    expect(applyExtendedTransformSchema(
      'md5_hash',
      { output_column: 'fingerprint' },
      input,
    ).at(-1)).toMatchObject({ name: 'fingerprint', type: 'str' });

    expect(applyExtendedTransformSchema(
      'uuid_generator',
      { output_column: 'record_uuid' },
      input,
    ).at(-1)).toMatchObject({ name: 'record_uuid', type: 'str' });
  });

  it('ricostruisce lo schema di melt con i nomi output personalizzati', () => {
    const output = applyExtendedTransformSchema(
      'melt',
      {
        id_columns: ['id'],
        value_columns: ['gennaio', 'febbraio'],
        var_name: 'mese',
        value_name: 'importo',
      },
      [column('id', 'int'), column('gennaio', 'real'), column('febbraio', 'real')],
    );

    expect(output).toEqual([
      column('id', 'int'),
      expect.objectContaining({ name: 'mese', type: 'str' }),
      expect.objectContaining({ name: 'importo', type: 'real' }),
    ]);
  });

  it('mantiene separati gli input di geo_nearest e applica i suffissi', () => {
    const left = [column('id', 'int'), column('geometry', 'geometry:point')];
    const right = [
      column('id', 'int'),
      column('nome'),
      column('geometry', 'geometry:point'),
    ];
    const output = applyExtendedTransformSchema(
      'geo_nearest',
      { output_column: 'distanza' },
      [...left, ...right],
      [left, right],
    );

    expect(output.map(col => col.name)).toEqual([
      'id',
      'geometry',
      'id_right',
      'nome',
      'distanza',
    ]);
  });

  it('aggiunge metadata deterministici a table_diff', () => {
    const left = [column('id', 'int'), column('valore'), column('solo_vecchio')];
    const right = [column('key', 'int'), column('valore'), column('solo_nuovo')];
    const output = applyExtendedTransformSchema(
      'table_diff',
      { left_keys: ['id'], right_keys: ['key'] },
      [...left, ...right],
      [left, right],
    );

    expect(output.map(col => col.name)).toEqual([
      'id',
      'valore',
      'solo_vecchio',
      'solo_nuovo',
      '_diff_status',
      '_diff_columns',
      '_diff_old_values',
    ]);
  });
});
