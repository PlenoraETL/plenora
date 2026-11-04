import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  distinctNodeValues: vi.fn(),
}));

import useCanvasStore from '../../src/store/useCanvasStore';
import { distinctNodeValues } from '../../src/api/projects';
import ValueMappingInput from '../../src/components/transforms/parameter-inputs/ValueMappingInput';
import ParameterInput from '../../src/components/transforms/ParameterInput';
import {
  FILL_LIMIT, describeFill, dropOrphanProposals, fillSourceKey, isEmptyRow, isUsableValue, mergePresentValues,
  toSavedRows,
} from '../../src/utils/lookupFill';
import { serializeCanvas } from '../../src/utils/canvasSerializer';

/**
 * Contract Blocco A, F3 (raccolta c): «Riempi con i valori presenti» della
 * Mappa valori. Valori dal server sul canvas CORRENTE (non salvato), colonna
 * per nome e id, tetto FILL_LIMIT; aggiunge solo i mancanti, null distinto,
 * campione/troncamento/motivo mostrati, errori del server mostrati, sola
 * lettura spenta.
 */
const n = (id, type, data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const LOOKUP_DEF = {
  parameters: [
    { id: 'column', type: 'column', label: 'Colonna da mappare' },
    { id: 'mapping', type: 'value_mapping', label: 'Mappatura valori' },
  ],
};
const reply = (over = {}) => ({
  values: [
    { value: 'A', is_null: false, count: 3 },
    { value: 'B', is_null: false, count: 2 },
    { value: null, is_null: true, count: 4 },
    { value: '1', is_null: false, count: 1 },
  ],
  column: 'zona', column_id: 'col-zona', truncated: false, sampled: false, sampled_rows: 10, reason: null,
  ...over,
});

let written;
function renderMapping({ value = [{ from: 'A', to: 'Centro' }], props = {} } = {}) {
  written = vi.fn();
  return render(<ValueMappingInput value={value} onChange={written} nodeId="lk" column="zona" columnId="col-zona" {...props} />);
}
const fillBtn = () => screen.getByRole('button', { name: /Riempi con i valori presenti|Lettura dei valori/ });
const fill = () => act(async () => { fireEvent.click(fillBtn()); });

beforeEach(() => {
  distinctNodeValues.mockReset();
  const st = useCanvasStore.getState();
  st.reset();
  st.setReadOnly(false);
  st.init('p1', 'manipola');
  st.loadGraph([n('s', 'project_source'), n('lk', 'transform', { transformType: 'lookup' }), n('o', 'result')], []);
});

describe('mergePresentValues / describeFill (one rule)', () => {
  it('adds only the missing values, at the end, as neutral proposals; null kept apart', () => {
    const { rows, added, skipped } = mergePresentValues([{ from: 'A', to: 'Centro' }, { from: '', to: '' }], reply().values);
    expect(added).toEqual(['B', '1']);
    expect(rows).toEqual([{ from: 'A', to: 'Centro' }, { from: 'B', to: '', proposed: true, source: null }, { from: '1', to: '', proposed: true, source: null }]);
    expect(skipped).toEqual({ nulls: 4, blanks: 0, blankValues: 0 });
    expect(describeFill(reply(), 2, skipped)).toMatch(/null\) compare in 4 righe/);
    expect(describeFill(reply(), 2, skipped)).toMatch(/da tradurre.*il risultato non cambia/);
  });

  it('tells sample, truncation and the server reason', () => {
    expect(describeFill(reply({ sampled: true, truncated: true, sampled_rows: 10000 }), 1)).toMatch(/campione di 10\.000 righe/);
    expect(describeFill(reply({ truncated: true }), 1)).toMatch(/la colonna ne ha altri/);
    expect(describeFill({ values: [], truncated: false, sampled: false, sampled_rows: 0, reason: 'Input file runtime: dati non disponibili nel canvas' }, 0))
      .toBe('Nessun valore letto: Input file runtime: dati non disponibili nel canvas.');
  });
});

describe('the «usable value» rule (one rule for fill, empty row, validation and save)', () => {
  // Enumerazione: ogni forma di testo, una sola risposta, uguale per tutti i consumatori.
  // Stringa non vuota, SENZA trim: il backend confronta le chiavi esattamente.
  const CASES = [['', false], [null, false], [undefined, false],
    [' ', true], ['   ', true], ['\t', true], ['a', true], [' a ', true], ['0', true], ['None', true]];
  it.each(CASES)('%j usable = %s everywhere', (text, usable) => {
    expect(isUsableValue(text)).toBe(usable);
    expect(isEmptyRow({ from: text, to: '' })).toBe(!usable);
    const merged = mergePresentValues([], [{ value: text, is_null: false, count: 3 }]);
    expect(merged.added.length).toBe(usable ? 1 : 0);
    expect(merged.skipped.blanks).toBe(usable ? 0 : 3);
    expect(toSavedRows([{ from: text, to: '' }]).length).toBe(usable ? 1 : 0);
  });

  it('the empty text is counted and told to the user, like the null; spaces are real values', () => {
    const values = [{ value: '', is_null: false, count: 2 }, { value: '  ', is_null: false, count: 5 }, { value: 'A', is_null: false, count: 1 }];
    const { added, skipped } = mergePresentValues([], values);
    expect(added).toEqual(['  ', 'A']);
    expect(skipped).toEqual({ nulls: 0, blanks: 2, blankValues: 1 });
    expect(describeFill({ values }, 2, skipped)).toMatch(/Il testo vuoto compare in 2 righe/);
  });

  it('a legacy map with a key of only spaces stays editable and savable (no error)', () => {
    renderMapping({ value: { ' ': 'spazio', a: 'A' } });
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[1], { target: { value: 'Alfa' } });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(written).toHaveBeenLastCalledWith([{ from: ' ', to: 'spazio' }, { from: 'a', to: 'Alfa' }]);
  });

  it('proposals never reach the saved map', () => {
    expect(toSavedRows([{ from: 'A', to: 'X' }, { from: 'B', to: '', proposed: true }, { from: 'C', to: '' },
      { from: 'D', to: '', proposed: true }, { from: 'E', to: 'Ei', proposed: true }, { from: 'F', to: ' ', proposed: true }]))
      .toEqual([{ from: 'A', to: 'X' }, { from: 'C', to: '' }, { from: 'E', to: 'Ei' }, { from: 'F', to: ' ' }]);
  });
});

describe('«Riempi con i valori presenti»', () => {
  it('asks the server on the CURRENT (unsaved) canvas with column, column_id and the limit', async () => {
    // Modifica non salvata: arriva al server cosi' com'e'.
    act(() => useCanvasStore.getState().updateNodeData('s', { label: 'non-salvato' }));
    distinctNodeValues.mockResolvedValue(reply());
    renderMapping();
    await fill();
    expect(distinctNodeValues).toHaveBeenCalledTimes(1);
    const [pid, nodeId, body] = distinctNodeValues.mock.calls[0];
    expect([pid, nodeId]).toEqual(['p1', 'lk']);
    expect(body).toMatchObject({ column: 'zona', columnId: 'col-zona', limit: FILL_LIMIT });
    expect(body.canvas.nodes.find(x => x.id === 's').label).toBe('non-salvato');
    expect(FILL_LIMIT).toBe(200);
  });

  it('adds the missing entries as proposals: nothing is written until «diventa» is written', async () => {
    distinctNodeValues.mockResolvedValue(reply());
    renderMapping();
    await fill();
    expect(written).not.toHaveBeenCalled();
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A', 'B', '1']);
    expect(screen.getAllByLabelText('Nuovo valore').map(i => i.placeholder)).toEqual(['diventa', 'da tradurre', 'da tradurre']);
    expect(screen.getByRole('status').textContent).toMatch(/Aggiunti 2 valori da tradurre/);
    expect(screen.getByRole('status').textContent).toMatch(/null/);
    // Scrivere «diventa» rende la riga parte della mappa; le altre proposte restano fuori.
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[1], { target: { value: 'Borgo' } });
    expect(written).toHaveBeenLastCalledWith([{ from: 'A', to: 'Centro' }, { from: 'B', to: 'Borgo' }]);
  });

  it.each([['assente', undefined], ['keep', 'keep'], ['null', 'null'], ['value', 'value']])(
    'Valori non mappati = %s: the saved canvas after Riempi is identical until a row is filled in',
    async (_name, unmatched) => {
      distinctNodeValues.mockResolvedValue(reply());
      const config = { column: 'zona', mapping: [{ from: 'A', to: 'Centro' }], ...(unmatched ? { unmatched, default: '0' } : {}) };
      act(() => useCanvasStore.getState().patchNodeConfig('lk', config));
      const saved = () => {
        const st = useCanvasStore.getState();
        return JSON.stringify(serializeCanvas(st.nodes, st.edges, { mode: st.mode }));
      };
      const before = saved();
      const lkConfig = () => useCanvasStore.getState().nodes.find(x => x.id === 'lk').data.config;
      // Come il pannello: la mappa letta dallo store, scritta nello store.
      function Harness() {
        const mapping = useCanvasStore(st => st.nodes.find(x => x.id === 'lk').data.config.mapping);
        return <ValueMappingInput value={mapping} nodeId="lk" column="zona" columnId="col-zona"
          onChange={(next) => useCanvasStore.getState().patchNodeConfig('lk', { mapping: next })} />;
      }
      render(<Harness />);
      await fill();
      expect(screen.getAllByLabelText('Valore di partenza')).toHaveLength(3);
      expect(saved()).toBe(before);
      fireEvent.change(screen.getAllByLabelText('Nuovo valore')[2], { target: { value: 'uno' } });
      expect(lkConfig().mapping).toEqual([{ from: 'A', to: 'Centro' }, { from: '1', to: 'uno' }]);
      expect(saved()).not.toBe(before);
      // Cancellare «diventa» la riporta a proposta: il canvas torna com'era.
      fireEvent.change(screen.getAllByLabelText('Nuovo valore')[2], { target: { value: '' } });
      expect(saved()).toBe(before);
    },
  );

  it('edits made while the values are being read are kept (merge on the current rows)', async () => {
    let resolve;
    distinctNodeValues.mockReturnValue(new Promise(r => { resolve = r; }));
    renderMapping();
    fireEvent.click(fillBtn());
    // Durante la lettura: si cambia la riga esistente e se ne aggiunge una.
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[0], { target: { value: 'Nord' } });
    fireEvent.click(screen.getByRole('button', { name: /Aggiungi riga/ }));
    fireEvent.change(screen.getAllByLabelText('Valore di partenza')[1], { target: { value: 'Z' } });
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[1], { target: { value: 'Zeta' } });
    await act(async () => { resolve(reply()); });
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A', 'Z', 'B', '1']);
    expect(screen.getAllByLabelText('Nuovo valore').map(i => i.value)).toEqual(['Nord', 'Zeta', '', '']);
    expect(written).toHaveBeenLastCalledWith([{ from: 'A', to: 'Nord' }, { from: 'Z', to: 'Zeta' }]);
  });

  it('the empty text is not a row (counted and told); a value of only spaces is a row', async () => {
    distinctNodeValues.mockResolvedValue(reply({ values: [{ value: '', is_null: false, count: 2 }, { value: '   ', is_null: false, count: 1 }] }));
    renderMapping();
    await fill();
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A', '   ']);
    expect(screen.getByRole('status').textContent).toMatch(/Il testo vuoto compare in 2 righe/);
  });

  it('Riempi + edits during the read: exactly the writes needed (no identical second write)', async () => {
    let resolve;
    distinctNodeValues.mockReturnValue(new Promise(r => { resolve = r; }));
    act(() => useCanvasStore.getState().patchNodeConfig('lk', { column: 'zona', mapping: [{ from: 'A', to: 'Centro' }] }));
    const writes = [];
    // Come il pannello: la mappa letta dallo store, scritta nello store.
    function Harness() {
      const mapping = useCanvasStore(st => st.nodes.find(x => x.id === 'lk').data.config.mapping);
      return <ValueMappingInput value={mapping} nodeId="lk" column="zona" columnId="col-zona"
        onChange={(next) => { writes.push(next); useCanvasStore.getState().patchNodeConfig('lk', { mapping: next }); }} />;
    }
    render(<Harness />);
    fireEvent.click(fillBtn());
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[0], { target: { value: 'Nord' } });
    fireEvent.click(screen.getByRole('button', { name: /Aggiungi riga/ }));
    fireEvent.change(screen.getAllByLabelText('Valore di partenza')[1], { target: { value: 'Z' } });
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[1], { target: { value: 'Zeta' } });
    const during = [
      [{ from: 'A', to: 'Nord' }],
      [{ from: 'A', to: 'Nord' }, { from: 'Z', to: '' }],
      [{ from: 'A', to: 'Nord' }, { from: 'Z', to: 'Zeta' }],
    ];
    expect(writes).toEqual(during);
    // Le proposte non cambiano il salvato: nessuna scrittura al ritorno.
    await act(async () => { resolve(reply()); });
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A', 'Z', 'B', '1']);
    expect(writes).toEqual(during);
  });

  it('proposals are tied to the column: a column change drops the ones not filled in, keeps the rest', async () => {
    distinctNodeValues.mockResolvedValue(reply());
    const { rerender } = renderMapping();
    await fill();
    fireEvent.change(screen.getAllByLabelText('Nuovo valore')[1], { target: { value: 'Borgo' } });
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A', 'B', '1']);
    const writesBefore = written.mock.calls.length;
    rerender(<ValueMappingInput value={written.mock.calls.at(-1)[0]} onChange={written} nodeId="lk" column="quartiere" columnId="col-quartiere" />);
    // Restano A (scritta a mano) e B (proposta compilata); va via 1 (proposta vuota).
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A', 'B']);
    expect(written.mock.calls.length).toBe(writesBefore);
  });

  it('a read that comes back after the column changed adds nothing', async () => {
    let resolve;
    distinctNodeValues.mockReturnValue(new Promise(r => { resolve = r; }));
    const { rerender } = renderMapping();
    fireEvent.click(fillBtn());
    rerender(<ValueMappingInput value={[{ from: 'A', to: 'Centro' }]} onChange={written} nodeId="lk" column="quartiere" columnId="col-quartiere" />);
    await act(async () => { resolve(reply()); });
    expect(screen.getAllByLabelText('Valore di partenza').map(i => i.value)).toEqual(['A']);
    expect(screen.getByRole('status').textContent).toMatch(/La colonna è cambiata durante la lettura/);
    expect(written).not.toHaveBeenCalled();
  });

  it('dropOrphanProposals / fillSourceKey (one rule)', () => {
    expect(fillSourceKey({ column: 'zona', columnId: 'c1' })).toBe('id:c1');
    expect(fillSourceKey({ column: 'zona' })).toBe('name:zona');
    expect(fillSourceKey({})).toBeNull();
    const rows = [{ from: 'A', to: 'X' }, { from: 'B', to: '', proposed: true, source: 'id:c1' },
      { from: 'C', to: 'Ci', proposed: true, source: 'id:c1' }, { from: 'D', to: '', proposed: true, source: 'id:c2' }];
    expect(dropOrphanProposals(rows, 'id:c2')).toEqual([rows[0], rows[2], rows[3]]);
    const same = [rows[0]];
    expect(dropOrphanProposals(same, 'id:c9')).toBe(same);
  });

  it('nothing new: nothing written, it says so', async () => {
    distinctNodeValues.mockResolvedValue(reply({ values: [{ value: 'A', is_null: false, count: 1 }] }));
    renderMapping();
    await fill();
    expect(written).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toMatch(/Nessun valore nuovo/);
  });

  it('runtime file input: no data, the server reason is shown', async () => {
    distinctNodeValues.mockResolvedValue({ values: [], truncated: false, sampled: false, sampled_rows: 0, reason: 'Input file runtime: dati non disponibili nel canvas' });
    renderMapping();
    await fill();
    expect(screen.getByRole('status').textContent).toMatch(/Input file runtime/);
    expect(written).not.toHaveBeenCalled();
  });

  it('truncated and sampled are shown', async () => {
    distinctNodeValues.mockResolvedValue(reply({ truncated: true, sampled: true, sampled_rows: 10000 }));
    renderMapping();
    await fill();
    expect(screen.getByRole('status').textContent).toMatch(/campione di 10\.000 righe/);
  });

  it('4xx: the server message is shown, the mapping is untouched', async () => {
    distinctNodeValues.mockRejectedValue(Object.assign(new Error('Request failed with status code 422'), { response: { status: 422, data: { detail: "Colonna 'zona' non univoca nell'ingresso del nodo" } } }));
    renderMapping();
    await fill();
    expect(screen.getByRole('alert').textContent).toMatch(/Valori non letti: Colonna 'zona' non univoca/);
    expect(written).not.toHaveBeenCalled();
  });

  it('network error: an Italian message, never the raw "Network Error"', async () => {
    distinctNodeValues.mockRejectedValue(Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' }));
    renderMapping();
    await fill();
    expect(screen.getByRole('alert').textContent).toMatch(/Impossibile contattare il server/);
    expect(screen.getByRole('alert').textContent).not.toMatch(/Network Error/);
  });

  it('read-only: the button is off (and so are the rows)', () => {
    act(() => useCanvasStore.getState().setReadOnly(true));
    renderMapping();
    expect(fillBtn().disabled).toBe(true);
    expect(screen.getAllByLabelText('Valore di partenza')[0].disabled).toBe(true);
  });

  it('no column chosen yet: the button is off', () => {
    renderMapping({ props: { column: null, columnId: null } });
    expect(fillBtn().disabled).toBe(true);
  });

  it('ParameterInput passes the Lookup column (name and id) and the step', async () => {
    distinctNodeValues.mockResolvedValue(reply());
    render(
      <ParameterInput
        param={LOOKUP_DEF.parameters[1]}
        value={[]}
        onChange={() => {}}
        targetNodeId="lk"
        transformDefinition={LOOKUP_DEF}
        config={{ column: 'zona', column_column_id: 'col-zona', mapping: [] }}
      />,
    );
    await fill();
    await waitFor(() => expect(distinctNodeValues).toHaveBeenCalled());
    expect(distinctNodeValues.mock.calls[0][1]).toBe('lk');
    expect(distinctNodeValues.mock.calls[0][2]).toMatchObject({ column: 'zona', columnId: 'col-zona' });
  });
});
