/**
 * Riscrittura dei riferimenti nelle espressioni (Formula e parametri
 * `expression`, components/transforms/DynamicConfig): regola unica di
 * utils/expressionRefs. Si riscrive solo cio' che si risolve in modo
 * univoco, per id; con le colonne del server (source_name sempre presente)
 * a valle di un Join con nomi doppi `name` non diventa mai `name_R`.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import {
  collectExpressionRefs, expressionColumnRefs, resolveColumnToken, rewriteExpressionNames, rewriteExpressionRefs,
} from '../../src/utils/expressionRefs';

// Fase 5, T6, R3.12: lo stesso corpus del backend (generato da
// app/shared/execution/expression_refs.py, verificato contro `ast`).
const TOKEN_CASES = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/tests2/contracts/expression_refs_cases.json'), 'utf-8'));

describe('parita\' col tokenizzatore del backend: solo i riferimenti, mai i letterali', () => {
  it.each(TOKEN_CASES.map(c => [c.expression || '<vuota>', c]))('%s', (_, c) => {
    expect(expressionColumnRefs(c.expression)).toEqual(c.refs);
    for (const rw of c.rewrites) {
      expect(rewriteExpressionNames(c.expression, { [rw.old]: rw.new })).toBe(rw.expected);
    }
  });

  // Fase 5, T5+T6, seconda correzione. REGOLA: rinomina SIMULTANEA ovunque
  // (backend rewrite_expression_refs, qui rewriteExpressionNames): stessi
  // risultati del corpus, scambi e catene compresi, anche dal percorso del
  // pannello (rewriteExpressionRefs con riferimenti salvati per id).
  it.each(TOKEN_CASES.map(c => [c.expression || '<vuota>', c]))('simultanea: %s', (_, c) => {
    const { renames, expected } = c.simultaneous;
    expect(rewriteExpressionNames(c.expression, renames)).toBe(expected);
    const olds = Object.keys(renames);
    const refs = olds.map((old, i) => ({ id: `c${i}`, name: old }));
    const columns = olds.map((old, i) => ({ id: `c${i}`, name: renames[old] }));
    expect(rewriteExpressionRefs(c.expression, refs, columns)).toBe(expected);
  });

  it('il corpus ha scambi e catene', () => {
    const swap = TOKEN_CASES.find(c => c.expression === "a + b == 'a'");
    expect(swap.simultaneous.expected).toBe("b + a == 'a'");
    expect(TOKEN_CASES.find(c => c.expression === 'a + b').simultaneous.expected).toBe('b + c');
  });

  it('scenario dell\'audit: stato == \'stato\' con stato -> status', () => {
    const columns = [{ id: 'c1', name: 'status', source_name: 'stato' }];
    expect(rewriteExpressionRefs("stato == 'stato'", [{ id: 'c1', name: 'stato' }], columns)).toBe("status == 'stato'");
    // Il letterale non conta come riferimento.
    expect(collectExpressionRefs("x == 'status'", columns)).toEqual([]);
  });
});

vi.mock('../../src/store/useCanvasStore', () => {
  const state = { edges: [], nodes: [] };
  const hook = (selector) => selector(state);
  hook.getState = () => state;
  return { default: hook };
});

import DynamicConfig from '../../src/components/transforms/DynamicConfig';
import { columnsConfirmed } from '../../src/utils/columnSource';

const col = (name, id, source_name = name) => ({ id, name, type: 'str', source_name, source_node: 'j' });
// Uscita di un Join con nomi doppi, come la manda il server.
const JOINED = [col('id', 'c_id'), col('name', 'c_left'), col('name_R', 'c_right', 'name')];

describe('regola unica: risoluzione univoca', () => {
  it('il nome attuale vince; il nome di origine vale solo se unico e non e\' il nome di un\'altra colonna', () => {
    expect(resolveColumnToken('name', JOINED).id).toBe('c_left');
    expect(resolveColumnToken('name_R', JOINED).id).toBe('c_right');
    const renamed = [col('citta', 'c1', 'city'), col('pop', 'c2')];
    expect(resolveColumnToken('city', renamed).id).toBe('c1');
    // Due colonne dichiarano lo stesso nome di origine: ambiguo.
    expect(resolveColumnToken('x', [col('x_1', 'a', 'x'), col('x_2', 'b', 'x')])).toBeNull();
    // Due colonne con lo stesso nome attuale: ambiguo.
    expect(resolveColumnToken('d', [col('d', 'a'), col('d', 'b')])).toBeNull();
  });

  it('riferimenti raccolti solo per i nomi risolti', () => {
    expect(collectExpressionRefs('name + 1', JOINED)).toEqual([{ id: 'c_left', name: 'name' }]);
    expect(collectExpressionRefs('`name_R` || name', JOINED)).toEqual([
      { id: 'c_left', name: 'name' }, { id: 'c_right', name: 'name_R' },
    ]);
  });
});

describe('riscrittura', () => {
  it.each([
    ['progetto vecchio (senza riferimenti) a valle di un Join con nomi doppi', 'name', [], 'name'],
    ['progetto vecchio: nome di origine unico non piu\' presente', 'city * 2', [], 'citta * 2'],
    ['per id: rinomina a monte', 'pop + 1', [{ id: 'c2', name: 'pop' }], 'abitanti + 1'],
    ['per id: il nome vecchio e\' oggi di UN\'ALTRA colonna -> non si tocca', 'name', [{ id: 'c_right', name: 'name' }], 'name'],
    ['ambiguo (due origini uguali) -> non si tocca', 'x', [], 'x'],
  ])('%s', (_, expression, refs, expected) => {
    const columns = [
      ...JOINED,
      col('citta', 'c1', 'city'), col('abitanti', 'c2', 'abitanti'),
      col('x_1', 'xa', 'x'), col('x_2', 'xb', 'x'),
    ];
    expect(rewriteExpressionRefs(expression, refs, columns)).toBe(expected);
  });
});

describe('DynamicConfig: Formula senza formula_column_refs a valle di un Join con nomi doppi', () => {
  it('non riscrive `name` in `name_R` (scrive solo i riferimenti risolti)', () => {
    const registry = {
      formula: { name: 'Formula', parameters: [{ id: 'formula', type: 'expression', label: 'Formula' }] },
    };
    const patches = [];
    render(
      <DynamicConfig
        transformId="formula"
        config={{ formula: 'name' }}
        updateConfig={p => patches.push(p)}
        registry={registry}
        inputColumns={JOINED}
        columnsConfirmed
        targetNodeId="f"
      />,
    );
    expect(patches.some(p => typeof p.formula === 'string' && p.formula !== 'name')).toBe(false);
    const refs = patches.map(p => p.formula_column_refs).filter(Boolean).at(-1);
    expect(refs).toEqual([{ id: 'c_left', name: 'name' }]);
  });
});

describe('DynamicConfig: nessuna correzione automatica su colonne non confermate (columnsConfirmed)', () => {
  const registry = {
    expr_op: { name: 'Espressione', parameters: [
      { id: 'expr', type: 'expression', label: 'Espressione' },
      { id: 'geom', type: 'geometry_column', label: 'Geometria' },
      { id: 'col', type: 'column', label: 'Colonna' },
    ] },
  };
  const config = { expr: 'a * 2', expr_column_refs: [{ id: 'ia', name: 'a' }], col: 'a' };

  it.each([
    ['prima della risposta (ingresso vuoto)', []],
    ['colonne provvisorie dichiarate', [col('a', 'ia'), { ...col('g', 'ig'), type: 'geometry' }]],
  ])('%s: riferimenti, id e geometria non si toccano', (_label, columns) => {
    const patches = [];
    render(
      <DynamicConfig
        transformId="expr_op"
        config={config}
        updateConfig={p => patches.push(p)}
        registry={registry}
        inputColumns={columns}
        geometryColumns={columns.filter(c => c.type === 'geometry')}
        columnsConfirmed={false}
        targetNodeId="x"
      />,
    );
    expect(patches).toEqual([]);
  });

  it('confermate con la colonna rinominata a monte: riscrive per id, non azzera i riferimenti', () => {
    const patches = [];
    render(
      <DynamicConfig
        transformId="expr_op"
        config={config}
        updateConfig={p => patches.push(p)}
        registry={registry}
        inputColumns={[col('b', 'ia')]}
        columnsConfirmed
        targetNodeId="x"
      />,
    );
    expect(patches.some(p => p.expr === 'b * 2')).toBe(true);
    expect(patches.some(p => Array.isArray(p.expr_column_refs) && p.expr_column_refs.length === 0)).toBe(false);
  });
});

describe('columnsConfirmed: regola unica', () => {
  it.each([
    [{ known: true, status: 'ok', inputs: [{}] }, true],
    [{ known: true, status: 'ok' }, true],
    [{ known: true, status: 'loading', inputs: [{}] }, false],
    [{ known: true, status: 'error', inputs: [{}] }, false],
    [{ known: true, status: 'idle', inputs: [{}] }, false],
    [{ known: false, status: 'ok', inputs: [{}] }, false],
    [{ known: true, status: 'ok', inputs: [] }, false],
    [null, false],
  ])('%j -> %s', (result, expected) => {
    expect(columnsConfirmed(result)).toBe(expected);
  });
});
