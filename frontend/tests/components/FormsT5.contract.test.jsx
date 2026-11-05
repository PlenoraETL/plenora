/**
 * Fase 5 T5 — form e input: buffer locale, convertitore unico, form
 * dichiarativi. Una sezione per meccanismo, ciascuna enumera i casi.
 *
 *  1. FieldInput / useFieldBuffer: il testo resta mentre si scrive ("file_",
 *     la virgola), si risincronizza solo da fuori, un testo non valido non si
 *     consegna e l'errore si vede; il Wizard non va avanti (R8.1-R8.3, N4).
 *  2. ProjectInputNode: chiave, colonne attese che conservano tipi e
 *     facoltativo (N4), sola lettura con TUTTI i controlli spenti (N11).
 *  3. Pivot e Connection Set: righe dalla config (undo), chiave con buffer.
 *  4. Form dichiarativi: Data Source per tipo, utenti, schedule.
 *  5. Nomi generati unici, cataloghi unici, regole residue (R8.8, R8.11,
 *     R8.15-R8.19).
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { useState } from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';

const { canvasStateRef } = vi.hoisted(() => ({ canvasStateRef: { current: { nodes: [], edges: [] } } }));
vi.mock('@xyflow/react', () => ({
  ...globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }),
  useNodeId: () => null,
}));
vi.mock('../../src/api/http', () => {
  const pending = () => new Promise(() => {});
  return {
    default: {
      get: pending, post: pending, put: pending, patch: pending, delete: pending,
      interceptors: { request: { use: () => {} }, response: { use: () => {} } },
      defaults: { headers: { common: {} } },
    },
  };
});

import FieldInput from '../../src/components/common/FieldInput';
import Wizard, { FIELDS_INVALID } from '../../src/components/common/Wizard';
import * as codecs from '../../src/utils/fieldCodecs';
import useCanvasStore from '../../src/store/useCanvasStore';
import ProjectInputNode, { mergeExpectedColumns } from '../../src/components/nodes/ProjectInputNode';
import PivotMappingBuilder from '../../src/components/transforms/PivotMappingBuilder';
import { formErrors, formPayload, initialValues, visibleFields } from '../../src/utils/formSchema';
import { DS_FIELDS, DS_STATE_FIELDS } from '../../src/utils/dataSourceForm';
import { USER_ROLES, userFormRules, userPayload } from '../../src/pages/UserManager';
import { scheduleFields, schedulePayload } from '../../src/components/modals/ScheduleWizard';
import { uniqueName, newProjectInputData } from '../../src/utils/uniqueName';
import { OUTPUT_FORMATS, outputFormatOptions, initialOutputFormat } from '../../src/constants/outputFormats';
import { staticParamsAfter } from '../../src/components/api-conn/ApiConnectionWizard';
import { finalNames } from '../../src/components/transforms/parameter-inputs/RenameListInput';
import { effectiveColumnOrder } from '../../src/components/transforms/parameter-inputs/ColumnOrderInput';
import { joinProblems, joinDraftFromData } from '../../src/utils/estraiJoins';
import { columnTypeFamily } from '../../src/utils/columnUtils';
import { typeSelectable } from '../../src/pages/ConnectionManager';

afterEach(() => { cleanup(); });

function Harness({ codec, initial, onCommit, commitOn }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <FieldInput aria-label="campo" codec={codec} value={value} commitOn={commitOn}
        onCommit={(v) => { onCommit?.(v); setValue(v); }} />
      <button type="button" onClick={() => setValue(initial)}>ripristina</button>
      <output data-testid="valore">{JSON.stringify(value)}</output>
    </>
  );
}

// ── 1. buffer locale ────────────────────────────────────────────────────────
describe('FieldInput: buffer locale e convertitore unico', () => {
  it('chiave: "file_" resta mentre si scrive, al blur si mostra la forma salvata (R8.1)', async () => {
    const user = userEvent.setup();
    render(<Harness codec={codecs.key()} initial="" />);
    const input = screen.getByRole('textbox', { name: 'campo' });
    await user.type(input, 'file_');
    expect(input).toHaveValue('file_');
    await user.type(input, 'clienti');
    expect(input).toHaveValue('file_clienti');
    expect(screen.getByTestId('valore')).toHaveTextContent('"file_clienti"');
    await user.clear(input);
    await user.type(input, 'Dati_');
    await user.tab();
    expect(input).toHaveValue('dati');
  });

  it('elenco: la virgola appena digitata resta (R8.1)', async () => {
    const user = userEvent.setup();
    render(<Harness codec={codecs.list()} initial={[]} />);
    const input = screen.getByRole('textbox', { name: 'campo' });
    await user.type(input, 'a,');
    expect(input).toHaveValue('a,');
    expect(screen.getByTestId('valore')).toHaveTextContent('["a"]');
    await user.type(input, ' b');
    expect(screen.getByTestId('valore')).toHaveTextContent('["a","b"]');
  });

  it('testo non valido: non si consegna, errore dopo il blur, mai il default (R8.4)', async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Harness codec={codecs.integer({ min: 1, max: 100 })} initial={50} onCommit={onCommit} />);
    const input = screen.getByRole('textbox', { name: 'campo' });
    await user.clear(input);
    expect(input).toHaveValue('');           // non torna "100" o il minimo
    expect(screen.queryByRole('alert')).toBeNull();
    await user.tab();
    expect(screen.getByRole('alert')).toHaveTextContent('Campo obbligatorio');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    await user.type(input, '5');
    expect(input).toHaveValue('5');          // non "1005"
    expect(onCommit).toHaveBeenLastCalledWith(5);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('un cambio da fuori (undo, reset) vince sul testo; il proprio commit no', async () => {
    const user = userEvent.setup();
    render(<Harness codec={codecs.text()} initial="iniziale" />);
    const input = screen.getByRole('textbox', { name: 'campo' });
    await user.clear(input);
    await user.type(input, 'nuovo');
    expect(input).toHaveValue('nuovo');
    fireEvent.click(screen.getByRole('button', { name: 'ripristina' }));
    expect(input).toHaveValue('iniziale');
  });

  it('commitOn="blur": niente consegna a ogni tasto', async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Harness codec={codecs.text()} initial="" commitOn="blur" onCommit={onCommit} />);
    await user.type(screen.getByRole('textbox', { name: 'campo' }), 'canone');
    expect(onCommit).not.toHaveBeenCalled();
    await user.tab();
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith('canone');
  });

  it('Wizard: un campo con testo non valido blocca Avanti e Completa', async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    function W() {
      const [step, setStep] = useState(0);
      const [n, setN] = useState(10);
      return (
        <Wizard isOpen onClose={() => {}} title="T" step={step} onStepChange={setStep} onComplete={onComplete}
          steps={[
            { label: 'Uno', content: <FieldInput aria-label="numero" codec={codecs.integer({ min: 1 })} value={n} onCommit={setN} /> },
            { label: 'Due', content: <div>secondo</div> },
          ]} />
      );
    }
    render(<W />);
    const input = screen.getByRole('textbox', { name: 'numero' });
    await user.clear(input);
    await user.type(input, 'x');
    await user.click(screen.getByRole('button', { name: /Avanti/ }));
    expect(screen.getByText(FIELDS_INVALID)).toBeInTheDocument();
    expect(screen.queryByText('secondo')).toBeNull();
    await user.clear(input);
    await user.type(input, '3');
    await user.click(screen.getByRole('button', { name: /Avanti/ }));
    expect(screen.getByText('secondo')).toBeInTheDocument();
  });
});

// ── 2. ProjectInputNode ─────────────────────────────────────────────────────
function loadInput(data, { readOnly = false } = {}) {
  const s = useCanvasStore.getState();
  s.reset();
  s.init('p1', 'estrai');
  s.setReadOnly(readOnly);
  s.loadGraph([{ id: 'in1', type: 'project_input', position: { x: 0, y: 0 }, data }], []);
}
const nodeData = () => useCanvasStore.getState().nodes.find(n => n.id === 'in1').data;

describe('ProjectInputNode (R8.1, N4, N11)', () => {
  const FILE = { input_key: 'dati', label: 'Dati', input_kind: 'file', required: true,
    expected_columns: [{ name: 'codice', type: 'int', required: false }], validation: { accepted_extensions: ['csv'] } };

  it('le colonne attese conservano tipo e facoltativo; solo le nuove nascono testo obbligatorie (N4)', async () => {
    const user = userEvent.setup();
    loadInput(FILE);
    render(<ProjectInputNode id="in1" data={nodeData()} />);
    const cols = screen.getByPlaceholderText('codice, nome, data');
    expect(cols).toHaveValue('codice');
    await user.type(cols, ', nome');
    expect(cols).toHaveValue('codice, nome');
    expect(nodeData().expected_columns).toEqual([
      { name: 'codice', type: 'int', required: false },
      { name: 'nome', type: 'str', required: true },
    ]);
    expect(mergeExpectedColumns([{ name: 'a', type: 'date', required: false }], ['b', 'a']))
      .toEqual([{ name: 'b', type: 'str', required: true }, { name: 'a', type: 'date', required: false }]);
  });

  it('la chiave si scrive con "_" (R8.1)', async () => {
    const user = userEvent.setup();
    loadInput({ ...FILE, input_key: '' });
    render(<ProjectInputNode id="in1" data={nodeData()} />);
    await user.type(screen.getByPlaceholderText('es. file_clienti'), 'file_clienti');
    expect(nodeData().input_key).toBe('file_clienti');
  });

  it.each(['file', 'text', 'integer', 'select', 'connection'])('sola lettura (%s): ogni controllo e\' spento (N11)', (kind) => {
    loadInput({ ...FILE, input_kind: kind, validation: kind === 'select' ? { options: ['a'] } : {} }, { readOnly: true });
    const { container } = render(<ProjectInputNode id="in1" data={nodeData()} />);
    const controls = [...container.querySelectorAll('input, select, textarea, button')];
    expect(controls.length).toBeGreaterThan(2);
    controls.forEach(el => expect(el.matches(':disabled'), el.outerHTML.slice(0, 80)).toBe(true));
  });
});

// ── 3. Pivot e Connection Set ───────────────────────────────────────────────
describe('Pivot (R8.3, R1.9)', () => {
  it('scrivendo "canone" la colonna in uscita non resta "c"; le righe vengono dalla config', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<PivotMappingBuilder value={{}} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /Aggiungi mappatura/ }));
    await user.type(screen.getByRole('textbox', { name: 'Valore sorgente' }), 'canone');
    expect(onChange).not.toHaveBeenCalled();
    await user.tab();
    expect(onChange).toHaveBeenLastCalledWith({ canone: 'canone' });
    rerender(<PivotMappingBuilder value={{ canone: 'canone' }} onChange={onChange} />);
    expect(screen.getByRole('textbox', { name: 'Nome della colonna in uscita' })).toHaveValue('canone');
    // Undo da fuori: la config torna vuota e il pannello la segue.
    rerender(<PivotMappingBuilder value={{}} onChange={onChange} />);
    expect(screen.queryByRole('textbox', { name: 'Valore sorgente' })).toBeNull();
  });

  it('un valore sorgente gia\' mappato e\' un errore, non una sovrascrittura', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PivotMappingBuilder value={{ a: 'x' }} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /Aggiungi mappatura/ }));
    const inputs = screen.getAllByRole('textbox', { name: 'Valore sorgente' });
    await user.type(inputs[1], 'a');
    await user.tab();
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/già mappato/);
  });
});

// ── 4. form dichiarativi ────────────────────────────────────────────────────
describe('Data Source: schema per tipo = rendering, validazione, payload (R8.5)', () => {
  const TYPES = ['local', 'ftp', 'sftp', 'sharepoint'];

  it.each(TYPES)('%s: solo i suoi campi, obbligatori validati, payload = campi visibili', (type) => {
    const values = { type, ...initialValues(DS_STATE_FIELDS) };
    const visible = visibleFields(DS_FIELDS, values).map(f => f.name);
    expect(new Set(visible).size).toBe(visible.length);
    const errors = formErrors(DS_FIELDS, values, { mode: 'create' });
    expect(errors.name).toBe('Il nome è obbligatorio');
    if (type === 'ftp' || type === 'sftp') expect(errors.host).toBe('Campo obbligatorio');
    if (type === 'local') expect(errors.root_path).toBe('Campo obbligatorio');
    if (type === 'sharepoint') expect(errors.site_url).toMatch(/URL Sito/);
    // Riempiti gli obbligatori, il payload ha SOLO campi del tipo.
    const filled = { ...values, name: 'N', root_path: '/r', host: 'h', username: 'u', tenant_id: 't', client_id: 'c', client_secret: 's', site_url: 'https://x' };
    expect(formErrors(DS_FIELDS, filled, { mode: 'create' })).toEqual({});
    const payload = formPayload(DS_FIELDS, filled, { mode: 'create' });
    Object.keys(payload).forEach(k => expect(visible).toContain(k));
    TYPES.filter(t => t !== type).forEach(() => {
      const foreign = DS_STATE_FIELDS.map(f => f.name).filter(n => !visible.includes(n));
      foreign.forEach(n => expect(payload).not.toHaveProperty(n));
    });
  });

  it('modifica: un campo vuoto si svuota, un segreto vuoto resta invariato, porta non valida = errore', () => {
    const values = { type: 'ftp', ...initialValues(DS_STATE_FIELDS, { name: 'A', host: 'h', root_path: '', port: 21 }) };
    const payload = formPayload(DS_FIELDS, values, { mode: 'edit' });
    expect(payload.root_path).toBe('');
    expect(payload).not.toHaveProperty('password');
    expect(payload.port).toBe(21);
    expect(formErrors(DS_FIELDS, { ...values, port: '99999' }, { mode: 'edit' }).port).toMatch(/1 e 65535/);
  });
});

describe('Utenti: campi modificabili = whitelist del backend (R8.20)', () => {
  const form = { username: 'u', password: 'p', role: 'user', group_id: 'g', is_active: true };
  it.each([
    [false, false, ['username', 'password', 'role', 'group_id']],
    [false, true, ['username', 'password', 'role', 'group_id']],
    [true, false, ['is_active']],
    [true, true, ['role', 'group_id', 'is_active']],
  ])('isEdit=%s superadmin=%s -> %j', (isEdit, isSuperadmin, keys) => {
    expect(Object.keys(userPayload(form, userFormRules({ isEdit, isSuperadmin }))).sort()).toEqual([...keys].sort());
  });
  it('i ruoli sono quelli del backend (VALID_ROLES), i soli-superadmin ROLES_ADMIN_OR_ABOVE', () => {
    const backend = fs.readFileSync(path.resolve(import.meta.dirname, '../../../backend/app/shared/contracts/auth.py'), 'utf-8');
    const tuple = (name) => backend.match(new RegExp(`${name}: tuple\\[str, \\.\\.\\.\\] = \\(([^)]*)\\)`))[1].match(/"([a-z]+)"/g).map(s => s.replace(/"/g, ''));
    expect(USER_ROLES.map(r => r.value).sort()).toEqual(tuple('VALID_ROLES').sort());
    expect(USER_ROLES.filter(r => r.superOnly).map(r => r.value).sort()).toEqual(tuple('ROLES_ADMIN_OR_ABOVE').sort());
  });
});

describe('Schedule: schema unico (R8.13, N13)', () => {
  const base = {
    project_id: 'p1', cron_expression: '0 3 * * *', description: '', timezone: 'Europe/Rome', use_cache: false,
    concurrency_max_queue: 3, enable_destination: true, connection_id: 'c1', schema_name: '', table_name: 't',
    write_mode: 'upsert', match_columns: 'id, cf', batch_size: '1000', verify_destination_connection: true,
    max_retries: '3', retry_backoff_seconds: '60, 0, 900', append_conflicts: 'skip', log_skipped_rows: true, enabled: false,
  };
  it('payload convertito: numeri, elenchi con lo 0, schema predefinito, stato attivo conservato', () => {
    const fields = scheduleFields();
    expect(formErrors(fields, base)).toEqual({});
    const p = schedulePayload(base, fields);
    expect(p.schedule).toEqual({ cron_expression: '0 3 * * *', description: '', timezone: 'Europe/Rome', enabled: false });
    expect(p.destination).toEqual(expect.objectContaining({ schema_name: 'public', match_columns: ['id', 'cf'], batch_size: 1000 }));
    expect(p.on_error).toEqual(expect.objectContaining({ max_retries: 3, retry_backoff_seconds: [60, 0, 900] }));
  });
  it('cron non valido, batch fuori limite, backoff non numerico e fuso sconosciuto sono errori', () => {
    const errs = formErrors(scheduleFields(), { ...base, cron_expression: '0 24 * * *', batch_size: '5', retry_backoff_seconds: '60, x', timezone: 'Mars/Olympus' });
    expect(Object.keys(errs).sort()).toEqual(['batch_size', 'cron_expression', 'retry_backoff_seconds', 'timezone']);
    // Il fuso gia' salvato fuori elenco resta valido (si mostra e si salva com'e').
    expect(formErrors(scheduleFields({ keepTimezone: 'Mars/Olympus' }), { ...base, timezone: 'Mars/Olympus' })).toEqual({});
  });
});

// ── 5. nomi generati, cataloghi, regole residue ─────────────────────────────
describe('nomi generati unici e stabili (R8.19)', () => {
  const pin = (key, order) => ({ type: 'project_input', data: { input_key: key, order } });
  it('il primo libero, non il conteggio + 1; ordine dopo il piu\' alto', () => {
    expect(uniqueName('input', ['input_2'])).toBe('input_1');
    expect(uniqueName('count', ['count_1', 'count_2'])).toBe('count_3');
    expect(uniqueName('sum_x', ['sum_x'], { bareFirst: true })).toBe('sum_x_2');
    // Due input, cancellato il primo: il nuovo non duplica il secondo.
    const d = newProjectInputData([pin('input_2', 1)], { kind: 'file' });
    expect(d.input_key).toBe('input_1');
    expect(d.order).toBe(2);
    expect(newProjectInputData([pin('input_1', 0), pin('input_2', 1)], { kind: 'text' }).input_key).toBe('input_3');
    expect(newProjectInputData([pin('input_soglia', 0)], { kind: 'number', label: 'Input Soglia' }).input_key).toBe('input_soglia_2');
  });
  it('nessun `input_${...length + 1}` / `count_${...length + 1}` in src', () => {
    const root = path.resolve(import.meta.dirname, '../../src');
    const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap(e => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
    const offenders = walk(root).filter(f => /\.(jsx?)$/.test(f))
      .filter(f => /(input|count)_\$\{[^}]*length\s*\+\s*1\}|const ordinal = [^;]*length \+ 1/.test(fs.readFileSync(f, 'utf-8')));
    expect(offenders).toEqual([]);
  });
});

describe('catalogo unico dei formati (R8.11)', () => {
  it('ogni formato e\' tra quelli che il backend scrive', () => {
    const backend = fs.readFileSync(path.resolve(import.meta.dirname, '../../../backend/app/core/eseguibili/output_persistence.py'), 'utf-8');
    const supported = backend.match(/supported = \{([^}]*)\}/)[1].match(/"([a-z]+)"/g).map(s => s.replace(/"/g, ''));
    OUTPUT_FORMATS.forEach(f => expect(supported).toContain(f.value));
  });
  it('un default non supportato si vede ma non si sceglie, e non parte un\'esecuzione', () => {
    expect(outputFormatOptions('pdf')[0]).toEqual(expect.objectContaining({ value: 'pdf', disabled: true }));
    expect(initialOutputFormat('pdf')).toBe('xlsx');
    expect(initialOutputFormat('gpkg')).toBe('gpkg');
  });
  it('nessun altro elenco di formati di uscita in src', () => {
    // Eccezione dichiarata: il nodo Data Source in scrittura usa il writer
    // dei Data Source (csv/xlsx/geojson/gpkg), non il risultato di un'esecuzione.
    const ALLOWED = { 'components/nodes/DataSourceNode.jsx': 'formato di scrittura su Data Source (altro writer)' };
    const root = path.resolve(import.meta.dirname, '../../src');
    const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap(e => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
    const offenders = walk(root).filter(f => /\.(jsx?)$/.test(f) && !f.endsWith('outputFormats.js'))
      .filter(f => /<option value="(xlsx|pdf|zip)">|OUTPUT_FORMATS = \[|const FORMATS = \[/.test(fs.readFileSync(f, 'utf-8')))
      .map(f => path.relative(root, f).split(path.sep).join('/'))
      .filter(name => !(name in ALLOWED));
    expect(offenders).toEqual([]);
  });
});

describe('regole residue', () => {
  it('R8.8: i valori fissi seguono il parametro (rinomina, rimozione, mappato)', () => {
    const prev = { api_type: 'enricher', parameters: [{ name: 'q', mode: 'fixed' }, { name: 'k', mode: 'mapped' }], static_parameters: { q: '1', f: 'json' } };
    expect(staticParamsAfter(prev, 0, { name: 'query', mode: 'fixed' })).toEqual({ query: '1', f: 'json' });
    expect(staticParamsAfter(prev, 0, null)).toEqual({ f: 'json' });
    expect(staticParamsAfter(prev, 0, { name: 'q', mode: 'mapped' })).toEqual({ f: 'json' });
  });
  it('R8.15: i nomi finali restano unici (id e id_L)', () => {
    expect(finalNames([{ name: 'id' }, { name: 'id_L' }], [{ old_name: 'id_L', new_name: 'id' }])).toEqual(['id', 'id']);
  });
  it('R8.16: ordine salvato ∩ attuale, nuove in coda, rimosse fuori, nessun doppione', () => {
    expect(effectiveColumnOrder(['b', 'x', 'a', 'b'], [{ name: 'a' }, { name: 'b' }, { name: 'c' }])).toEqual(['b', 'a', 'c']);
    expect(effectiveColumnOrder(['b', 'a'], [])).toEqual(['b', 'a']);
  });
  it('R8.17: filtri ON a meta\' e filtri ON in un join spaziale non si scartano in silenzio', () => {
    const draft = (patch) => ({ ...joinDraftFromData({ source_column: 'id', target_column: 'id', join_conditions: [{ source_column: 'id', target_column: 'id' }] }), ...patch });
    expect(joinProblems(draft({ on_filters: [{ column: '', operator: '=', value: 'x' }] })).map(p => p.message).join()).toMatch(/Filtro ON 1: scegli la colonna/);
    expect(joinProblems(draft({ on_filters: [{ column: 'stato', operator: '=', value: '' }] })).map(p => p.message).join()).toMatch(/scrivi il valore/);
    expect(joinProblems(draft({ on_filters: [{ column: 'stato', operator: 'IS NULL', value: '' }] }))).toEqual([]);
    expect(joinProblems(draft({ mode: 'spatial', source_geom_column: 'g', target_geom_column: 'g', on_filters: [{ column: 'stato', operator: 'IS NULL' }] }))
      .some(p => /non usa i filtri ON/.test(p.message))).toBe(true);
  });
  it('R8.18: famiglia del tipo per gli operatori del filtro', () => {
    expect(['integer', 'int8', 'bigint', 'int'].map(columnTypeFamily)).toEqual(['int', 'int', 'int', 'int']);
    expect(['float', 'numeric(10,2)', 'double precision', 'real'].map(columnTypeFamily)).toEqual(['real', 'real', 'real', 'real']);
    expect(['timestamp', 'datetime', 'datetime64[ns]', 'date'].map(columnTypeFamily)).toEqual(['date', 'date', 'date', 'date']);
    expect(['varchar', 'character varying', 'text', 'interval'].map(columnTypeFamily)).toEqual(['str', 'str', 'str', 'str']);
    expect(columnTypeFamily('geometry:point:4326')).toBe('geometry');
  });
  it('R8.7: in modifica il tipo resta nella sua categoria', () => {
    expect(typeSelectable({ category: 'arcgis' }, 'database')).toBe(false);
    expect(typeSelectable({ category: 'database' }, 'database')).toBe(true);
    expect(typeSelectable({ category: 'arcgis' }, null)).toBe(true);
  });
});

describe('LoginPage: il codice MFA e\' dell\'utente scritto (R8.22)', () => {
  it('cambiando username si azzera la richiesta MFA', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/pages/LoginPage.jsx'), 'utf-8');
    expect(src).toMatch(/if \(next\.trim\(\) !== username\.trim\(\)\) \{ setNeedsMfa\(false\); setMfaCode\(''\); \}/);
  });
});
