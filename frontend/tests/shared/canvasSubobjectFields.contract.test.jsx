/**
 * Contratto dei sotto-oggetti del canvas, lato frontend (Fase 5, T5+T6,
 * seconda correzione).
 *
 * REGOLA: i campi che i pannelli scrivono negli elementi delle liste del
 * nodo salvate nel canvas (filters, aggregations, order_by_columns,
 * having_conditions) sono ESATTAMENTE quelli di
 * backend/tests2/contracts/canvas_subobject_fields.json: ogni elemento
 * salvato ne usa un sottoinsieme, e l'unione di cio' che le azioni scrivono e'
 * l'elenco intero (niente campi dichiarati e mai scritti). Il backend
 * (tests2/contracts/test_canvas_subobject_fields.py) verifica che il modello
 * Pydantic dichiari o conservi gli stessi campi.
 *
 * Si montano i pannelli veri (GroupOrderHavingPanel, FilterModal), si
 * eseguono le azioni che creano e modificano ogni sotto-oggetto e si legge
 * cio' che il canvas salva (serializeCanvas + JSON, come il PUT).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import GroupOrderHavingPanel from '../../src/components/nodes/result/standard/GroupOrderHavingPanel';
import FilterModal from '../../src/components/modals/FilterModal';
import { serializeCanvas } from '../../src/utils/canvasSerializer';
import { outputReferencesPatch } from '../../src/utils/columnPlans';

const CONTRACT = Object.fromEntries(Object.entries(JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/tests2/contracts/canvas_subobject_fields.json'), 'utf-8',
))).filter(([key]) => !key.startsWith('_')));
const FIELDS = Object.keys(CONTRACT);

const COLUMNS = [
  { name: 'pop', type: 'int' }, { name: 'area', type: 'real' },
  { name: 'nome', type: 'str' }, { name: 'data', type: 'date' },
];

// Chiavi scritte, per lista, su tutte le azioni del test.
const written = Object.fromEntries(FIELDS.map(f => [f, new Set()]));
const offenders = [];

/** Legge cio' che il canvas salva (come il PUT) e registra le chiavi. */
function recordSaved(label) {
  const { nodes, edges } = useCanvasStore.getState();
  const saved = JSON.parse(JSON.stringify(serializeCanvas(nodes, edges))).nodes[0];
  for (const field of FIELDS) {
    for (const item of saved[field] || []) {
      for (const key of Object.keys(item)) {
        written[field].add(key);
        if (!CONTRACT[field].includes(key)) offenders.push(`${label}: ${field}.${key}`);
      }
    }
  }
  return saved;
}

function reset(type, data) {
  const store = useCanvasStore.getState();
  store.reset();
  store.init('p1', 'estrai');
  store.loadGraph([{ id: 'n1', type, position: { x: 0, y: 0 }, data }], []);
}

function Panel() {
  const data = useCanvasStore(s => s.nodes.find(n => n.id === 'n1')?.data) || {};
  const update = useCanvasStore(s => s.updateNodeData);
  return (
    <GroupOrderHavingPanel
      data={data}
      id="n1"
      updateNodeData={update}
      isEstrai
      isManipulate={false}
      availableColumns={COLUMNS}
      manipolaColumns={[]}
    />
  );
}

const nodeData = () => useCanvasStore.getState().nodes[0].data;
const change = async (el, value) => { await act(async () => { fireEvent.change(el, { target: { value } }); }); };
const click = async (el) => { await act(async () => { fireEvent.click(el); }); };

beforeEach(() => { reset('resultNode', { fileName: 'out', format: 'csv', columns: COLUMNS }); });
afterEach(() => cleanup());

describe('i pannelli scrivono solo i campi del contratto dei sotto-oggetti', () => {
  it('il contratto elenca le quattro liste', () => {
    expect(FIELDS.sort()).toEqual(['aggregations', 'filters', 'having_conditions', 'order_by_columns']);
  });

  it('GROUP BY: aggiungi, cambia funzione/colonna, scrivi e svuota l\'alias, togli', async () => {
    const { container } = render(<Panel />);
    await click(container.querySelectorAll('.result-node__groupby-header')[0]);
    await click(screen.getAllByRole('checkbox')[0]);
    await change(container.querySelector('.result-node__groupby-dropdown'), 'nome');
    await click(screen.getByRole('button', { name: '+ Aggiungi aggregazione' }));
    recordSaved('aggiungi aggregazione');
    const row = () => container.querySelector('.result-node__aggregation-row');
    await change(row().querySelector('.result-node__agg-select--func'), 'SUM');
    await change(row().querySelector('.result-node__agg-select--col'), 'pop');
    expect(nodeData().aggregations[0]).toMatchObject({ function: 'SUM', column: 'pop', alias: 'sum_pop' });
    recordSaved('cambia funzione e colonna');
    await change(row().querySelector('.result-node__agg-alias'), 'abitanti');
    expect(recordSaved('alias dell\'utente').aggregations[0]).toEqual({ column: 'pop', function: 'SUM', alias: 'abitanti', alias_source: 'user' });
    await change(row().querySelector('.result-node__agg-alias'), '');
    recordSaved('alias svuotato');
    await click(screen.getByRole('button', { name: '+ Aggiungi aggregazione' }));
    await click(container.querySelector('.result-node__agg-remove'));
    recordSaved('togli aggregazione');
    expect(nodeData().aggregations).toHaveLength(1);
  });

  it('ORDER BY: aggiungi colonne e cambia direzione', async () => {
    const { container } = render(<Panel />);
    await click(container.querySelectorAll('.result-node__groupby-header')[1]);
    await click(screen.getAllByRole('checkbox')[0]);
    await change(container.querySelector('.result-node__groupby-dropdown'), 'pop');
    await change(container.querySelector('.result-node__groupby-dropdown'), 'area');
    recordSaved('aggiungi ordinamento');
    await click(container.querySelector('.result-node__orderby-dir-btn'));
    expect(recordSaved('direzione').order_by_columns).toEqual([
      { column: 'pop', direction: 'DESC' }, { column: 'area', direction: 'ASC' },
    ]);
  });

  it('HAVING: abilita, aggiungi, modifica ogni campo', async () => {
    reset('resultNode', {
      fileName: 'out', format: 'csv', columns: COLUMNS,
      group_by_enabled: true, group_by_columns: ['nome'],
      aggregations: [{ column: 'pop', function: 'SUM', alias: 'sum_pop' }],
    });
    const { container } = render(<Panel />);
    const having = () => [...container.querySelectorAll('.result-node__section')].at(-1);
    await click(having().querySelector('input[type="checkbox"]'));
    // L'abilitazione aggiunge la prima condizione se non c'e'.
    if (!having().querySelector('.result-node__aggregation-row')) {
      await click([...having().querySelectorAll('button')].find(b => /Aggiungi/.test(b.textContent)));
    }
    recordSaved('aggiungi condizione');
    const row = having().querySelector('.result-node__aggregation-row');
    const [fn, col, op] = row.querySelectorAll('select');
    await change(fn, 'SUM');
    await change(col, 'pop');
    await change(op, '>=');
    await change(row.querySelector('input'), '1000');
    expect(recordSaved('modifica condizione').having_conditions).toEqual([
      { function: 'SUM', column: 'pop', operator: '>=', value: '1000' },
    ]);
  });

  it('Filtri WHERE (FilterModal): aggiungi, scegli colonna, operatore, valore, salva', async () => {
    reset('tableNode', { table_name: 'comuni', columns: COLUMNS, filters: [] });
    const onSave = (filters) => useCanvasStore.getState().updateNodeData('n1', { filters });
    render(<FilterModal isOpen onClose={() => {}} nodeId="n1" filters={[]} columns={COLUMNS} tableName="comuni" onSave={onSave} />);
    const items = () => document.querySelectorAll('.filter-modal__filter-item');
    await change(items()[0].querySelectorAll('select')[0], 'pop');
    await change(items()[0].querySelectorAll('select')[1], '>');
    await change(items()[0].querySelector('input'), '5');
    await click(document.querySelector('.filter-modal__add-btn'));
    await change(items()[1].querySelectorAll('select')[0], 'nome');
    await change(items()[1].querySelectorAll('select')[1], 'IS NULL');
    await click(screen.getByRole('button', { name: /Salva filtri/ }));
    const saved = recordSaved('filtri');
    expect(saved.filters).toEqual([
      { column: 'pop', operator: '>', value: '5' },
      { column: 'nome', operator: 'IS NULL', value: '' },
    ]);
  });

  it('Filtri WHERE (FilterModal): un gruppo O scrive `group` (UI-3, U20)', async () => {
    reset('tableNode', { table_name: 'comuni', columns: COLUMNS, filters: [] });
    const onSave = (filters) => useCanvasStore.getState().updateNodeData('n1', { filters });
    render(<FilterModal isOpen onClose={() => {}} nodeId="n1" filters={[]} columns={COLUMNS} tableName="comuni" onSave={onSave} />);
    const items = () => document.querySelectorAll('.filter-modal__filter-item');
    await change(items()[0].querySelectorAll('select')[0], 'pop');
    await click(screen.getByRole('button', { name: /Aggiungi un gruppo O/ }));
    await change(items()[1].querySelectorAll('select')[0], 'nome');
    await click(screen.getByRole('button', { name: /Salva filtri/ }));
    const saved = recordSaved('filtri');
    expect(saved.filters).toEqual([
      { column: 'pop', operator: '=', value: '', group: 0 },
      { column: 'nome', operator: '=', value: '', group: 1 },
    ]);
  });

  it('colonne in uscita rinominate: le voci seguono senza campi nuovi', () => {
    const data = {
      output_columns: ['pop'],
      aggregations: [{ column: 'pop', function: 'SUM', alias: 'a', alias_source: 'user' }],
      order_by_columns: [{ column: 'pop', direction: 'ASC' }],
      having_conditions: [{ function: 'SUM', column: 'pop', operator: '>', value: '1' }],
    };
    const patch = outputReferencesPatch(data, [{ name: 'pop' }], [{ name: 'pop', alias: 'abitanti' }]);
    for (const field of ['aggregations', 'order_by_columns', 'having_conditions']) {
      for (const item of patch[field] || data[field]) {
        expect(Object.keys(item).filter(k => !CONTRACT[field].includes(k)), field).toEqual([]);
      }
    }
  });

  it('nessun campo fuori contratto; ogni campo del contratto e\' scritto da un\'azione', () => {
    expect(offenders).toEqual([]);
    for (const field of FIELDS) expect([...written[field]].sort(), field).toEqual([...CONTRACT[field]].sort());
  });
});
