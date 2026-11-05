import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ParameterInput from '../../../src/components/transforms/ParameterInput';

/**
 * Contract test per ParameterInput (974 LOC, 0 prior coverage).
 * Componente puro: param spec + value -> input renderizzato + onChange.
 *
 * Aree:
 *   1. string -> text input + onChange propagation
 *   2. number -> number input con min/max/step + parseFloat / null su empty
 *   3. boolean -> checkbox + label inline + onChange(checked)
 *   4. select -> options + "-- Seleziona --" se !required + onChange(value)
 *   5. column (single) + filter='numeric' -> ColumnSelector esclude
 *      colonne non numeriche
 *   6. columns (multiple) -> add via select + remove via × tag button
 *   7. string_list -> add via Enter + remove via × tag button
 *
 * PivotMappingBuilder NON e' testato (sub-component
 * autonomo, covered by its own contract tests).
 */

// PivotMappingBuilder e' importato top-level da ParameterInput; lo
// stub-iamo per non caricare la logica pivot in test che non la
// esercitano. Nessun test qui usa type='pivot_mapping'.
vi.mock(
  '../../../src/components/transforms/PivotMappingBuilder',
  () => ({ default: () => null }),
);

describe('ParameterInput — contract', () => {

  it('string type: renders text input with placeholder default and propagates onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ParameterInput
        param={{ id: 'q', type: 'string', label: 'Nome' }}
        value="foo"
        onChange={onChange}
      />,
    );

    // Placeholder default = "Inserisci nome" (label.toLowerCase).
    const input = screen.getByPlaceholderText('Inserisci nome');
    expect(input).toHaveValue('foo');

    // Append 'X' (1 char): onChange chiamato 1 volta col valore aggiornato
    // (controlled component: il parent non aggiorna value, quindi solo
    // l'ultima chiamata rappresenta lo state intent).
    await user.type(input, 'X');
    expect(onChange).toHaveBeenLastCalledWith('fooX');
  });

  it('schema_contract param: renders compact Schema badge with explanatory tooltip', () => {
    render(
      <ParameterInput
        param={{
          id: 'output_columns',
          type: 'string_list',
          label: 'Colonne output dichiarate',
          schema_contract: true,
        }}
        value={[]}
        onChange={vi.fn()}
      />,
    );

    const badge = screen.getByText('Schema');
    expect(badge).toHaveClass('parameter-input__schema-badge');
    expect(badge).toHaveAttribute(
      'title',
      "Schema previsto nel canvas: non cambia l'esecuzione.",
    );
  });


  it('number type: un convertitore per tipo, min/max applicati, null su vuoto (T5 R8.14)', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ParameterInput
        param={{
          id: 'age',
          type: 'number',
          label: 'Eta',
          min: 0,
          max: 120,
          step: 1,
        }}
        value={5}
        onChange={onChange}
      />,
    );

    const input = screen.getByRole('textbox', { name: 'Eta' });
    expect(input).toHaveValue('5');
    // Svuotato: null (parametro presente e vuoto).
    await user.clear(input);
    expect(onChange).toHaveBeenLastCalledWith(null);
    // Fuori dai limiti: errore del campo, nessuna scrittura.
    onChange.mockClear();
    await user.type(input, '500');
    expect(onChange).not.toHaveBeenCalledWith(500);
    await user.tab();
    expect(screen.getByText('Inserisci un valore tra 0 e 120')).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, '42');
    expect(onChange).toHaveBeenLastCalledWith(42);
  });

  it('default mostrato solo se il parametro manca; select obbligatoria senza valore non preseleziona (T5 R8.14)', () => {
    const { rerender } = render(
      <ParameterInput param={{ id: 'n', type: 'number', label: 'N', default: 7 }} value={undefined} onChange={vi.fn()} />,
    );
    expect(screen.getByRole('textbox', { name: 'N' })).toHaveValue('7');
    rerender(<ParameterInput param={{ id: 'n', type: 'number', label: 'N', default: 7 }} value={null} onChange={vi.fn()} />);
    expect(screen.getByRole('textbox', { name: 'N' })).toHaveValue('');
    rerender(
      <ParameterInput
        param={{ id: 'how', type: 'select', label: 'Come', required: true, options: ['inner', 'left'] }}
        value={undefined}
        onChange={vi.fn()}
      />,
    );
    const select = screen.getByRole('combobox');
    expect(select).toHaveValue('');
    expect(screen.getByRole('option', { name: '-- Seleziona --' })).toBeDisabled();
  });

  it('boolean type: checkbox with inline label + onChange(checked)', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ParameterInput
        param={{ id: 'enabled', type: 'boolean', label: 'Attivo' }}
        value={false}
        onChange={onChange}
      />,
    );

    // Label inline (non sopra), checkbox accessibile via name.
    const cb = screen.getByRole('checkbox', { name: 'Attivo' });
    expect(cb).not.toBeChecked();

    await user.click(cb);
    expect(onChange).toHaveBeenLastCalledWith(true);
  });


  it('select type: renders "-- Seleziona --" placeholder when not required and propagates choice', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ParameterInput
        param={{
          id: 'op',
          type: 'select',
          label: 'Operazione',
          options: ['sum', 'avg', 'count'],
          required: false,
        }}
        value=""
        onChange={onChange}
      />,
    );

    // Placeholder option presente (required=false).
    expect(
      screen.getByRole('option', { name: '-- Seleziona --' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'sum' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'avg' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'count' })).toBeInTheDocument();

    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'avg');
    expect(onChange).toHaveBeenLastCalledWith('avg');
  });


  it('column single + filter="numeric": only numeric columns appear in dropdown', () => {
    render(
      <ParameterInput
        param={{
          id: 'col',
          type: 'column',
          label: 'Colonna numerica',
          filter: 'numeric',
        }}
        value=""
        onChange={vi.fn()}
        columns={[
          { name: 'qty', type: 'int' },
          { name: 'price', type: 'real' },
          { name: 'name', type: 'str' },
          { name: 'created', type: 'date' },
        ]}
      />,
    );

    // Solo colonne numeric (int/real/float/number) compaiono come option.
    expect(
      screen.getByRole('option', { name: /qty \(int\)/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: /price \(real\)/ }),
    ).toBeInTheDocument();
    // Non-numeric esclusi: 'str' e 'date' non matchano [int|real|float|number].
    expect(
      screen.queryByRole('option', { name: /name \(str\)/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: /created \(date\)/ }),
    ).not.toBeInTheDocument();
  });


  it('columns multiple: select adds a tag, click × removes the tag', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const columnsList = [
      { name: 'a', type: 'str' },
      { name: 'b', type: 'int' },
    ];

    // Render iniziale con value=[]: aggiungi 'a' via select.
    const { rerender } = render(
      <ParameterInput
        param={{ id: 'cols', type: 'columns', label: 'Colonne' }}
        value={[]}
        onChange={onChange}
        columns={columnsList}
      />,
    );

    const addSelect = screen.getByRole('combobox');
    await user.selectOptions(addSelect, 'a');
    // ColumnSelector passa anche il secondo argomento `nextColumns`
    // (oggetti colonna tipizzati) accanto ai nomi selezionati.
    expect(onChange).toHaveBeenLastCalledWith(
      ['a'],
      [{ name: 'a', type: 'str' }],
    );

    // Re-render con value=['a']: tag visibile + click × → onChange([]).
    onChange.mockClear();
    rerender(
      <ParameterInput
        param={{ id: 'cols', type: 'columns', label: 'Colonne' }}
        value={['a']}
        onChange={onChange}
        columns={columnsList}
      />,
    );
    // Il tag 'a' deve essere visibile.
    expect(screen.getByText('a')).toBeInTheDocument();
    // Bottone × accessibile come role button con name '×'.
    await user.click(screen.getByRole('button', { name: '×' }));
    expect(onChange).toHaveBeenLastCalledWith([], []);
  });


  it('string_list: Enter adds a value, × removes a tag', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <ParameterInput
        param={{
          id: 'list',
          type: 'string_list',
          label: 'Lista',
          placeholder: 'Aggiungi valore',
        }}
        value={['foo']}
        onChange={onChange}
      />,
    );

    // Tag 'foo' visibile.
    expect(screen.getByText('foo')).toBeInTheDocument();

    // Type 'bar' + Enter → onChange(['foo','bar']).
    const input = screen.getByPlaceholderText('Aggiungi valore');
    await user.type(input, 'bar{Enter}');
    expect(onChange).toHaveBeenLastCalledWith(['foo', 'bar']);

    // Click × sul tag 'foo' → onChange([]).
    onChange.mockClear();
    rerender(
      <ParameterInput
        param={{
          id: 'list',
          type: 'string_list',
          label: 'Lista',
          placeholder: 'Aggiungi valore',
        }}
        value={['foo']}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole('button', { name: '×' }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
