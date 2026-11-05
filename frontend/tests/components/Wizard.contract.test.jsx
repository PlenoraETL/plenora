/**
 * Contract: Wizard (Fase 5, T4; R6.2, R6.3, R6.4, N7).
 *  - la barra degli step non salta uno step non valido (si ferma li' con il suo errore);
 *  - Completa controlla tutti gli step; un solo invio alla volta;
 *  - durante l'invio Esc, ×, Indietro e barra sono spenti;
 *  - chiudere con dati inseriti chiede conferma; senza dati chiude subito;
 *  - tornando indietro lo stato (del chiamante) resta.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { useState } from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import Wizard, { stepError, firstInvalidStep } from '../../src/components/common/Wizard';
import { answerConfirm } from '../helpers/confirmDialog';

afterEach(() => cleanup());

function Harness({ onComplete = vi.fn(), onClose = vi.fn(), initialStep = 0 }) {
  const [step, setStep] = useState(initialStep);
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const steps = [
    { label: 'Tipo', content: <div>tipo</div> },
    { label: 'Info', canAdvance: Boolean(name), content: <input aria-label="nome" value={name} onChange={e => setName(e.target.value)} /> },
    { label: 'Auth', validate: () => (token ? null : 'Token richiesto'), content: <input aria-label="token" value={token} onChange={e => setToken(e.target.value)} /> },
    { label: 'Fine', content: <div>fine</div> },
  ];
  return <Wizard isOpen onClose={onClose} title="W" step={step} onStepChange={setStep} steps={steps} onComplete={onComplete} />;
}

const stepButton = (label) => screen.getByRole('button', { name: new RegExp(label) });

describe('Wizard', () => {
  it('regola unica di validita\' degli step', () => {
    expect(stepError({ canAdvance: false })).toMatch(/Completa/);
    expect(stepError({ validate: () => 'X' })).toBe('X');
    expect(stepError({})).toBeNull();
    expect(firstInvalidStep([{}, { canAdvance: false }, {}], 3)).toBe(1);
    expect(firstInvalidStep([{}, { canAdvance: false }], 1)).toBe(-1);
  });

  it('R6.2: la barra degli step si ferma sul primo step non valido', () => {
    render(<Harness />);
    fireEvent.click(stepButton('Fine'));
    expect(screen.getByLabelText('nome')).toBeInTheDocument();
    expect(screen.getByRole('alert').textContent).toMatch(/Completa questo passo/);
    fireEvent.change(screen.getByLabelText('nome'), { target: { value: 'API' } });
    fireEvent.click(stepButton('Fine'));
    expect(screen.getByLabelText('token')).toBeInTheDocument();
    expect(screen.getByRole('alert').textContent).toMatch(/Token richiesto/);
    fireEvent.change(screen.getByLabelText('token'), { target: { value: 't' } });
    fireEvent.click(stepButton('Fine'));
    expect(screen.getByText('fine')).toBeInTheDocument();
  });

  it('tornando indietro lo stato resta', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /Avanti/ }));
    fireEvent.change(screen.getByLabelText('nome'), { target: { value: 'API' } });
    fireEvent.click(screen.getByRole('button', { name: /Avanti/ }));
    fireEvent.click(screen.getByRole('button', { name: /Indietro/ }));
    expect(screen.getByLabelText('nome').value).toBe('API');
  });

  it('R6.3: un solo invio; durante l\'invio tutto spento', async () => {
    let finish;
    const onComplete = vi.fn(() => new Promise((r) => { finish = r; }));
    const onClose = vi.fn();
    function Ready() {
      const [step, setStep] = useState(1);
      return <Wizard isOpen onClose={onClose} title="W" step={step} onStepChange={setStep} onComplete={onComplete}
        steps={[{ label: 'A', content: 'a' }, { label: 'B', content: 'b' }]} />;
    }
    render(<Ready />);
    const save = screen.getByRole('button', { name: 'Salva' });
    await act(async () => { fireEvent.click(save); fireEvent.click(save); });
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Chiudi' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Indietro/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Attendere/ })).toBeDisabled();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => { finish(); });
    expect(screen.getByRole('button', { name: 'Salva' })).not.toBeDisabled();
  });

  it('Completa non parte con uno step precedente non valido', async () => {
    const onComplete = vi.fn();
    render(<Harness initialStep={3} onComplete={onComplete} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Salva' })); });
    expect(onComplete).not.toHaveBeenCalled();
    expect(screen.getByLabelText('nome')).toBeInTheDocument();
  });

  it('R6.4: Esc senza dati chiude; con dati inseriti chiede e "Continua" tiene il wizard', async () => {
    const onClose = vi.fn();
    const { unmount } = render(<Harness onClose={onClose} />);
    await act(async () => { fireEvent.keyDown(document.body, { key: 'Escape' }); });
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();

    const onClose2 = vi.fn();
    render(<Harness onClose={onClose2} initialStep={1} />);
    fireEvent.change(screen.getByLabelText('nome'), { target: { value: 'API' } });
    await act(async () => { fireEvent.keyDown(screen.getByLabelText('nome'), { key: 'Escape' }); });
    expect(onClose2).not.toHaveBeenCalled();
    expect(screen.getByText(/I dati inseriti/)).toBeInTheDocument();
    await answerConfirm(false);
    expect(onClose2).not.toHaveBeenCalled();
    expect(screen.getByLabelText('nome').value).toBe('API');
    // × con dati: chiede; "Chiudi senza salvare" chiude.
    await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'Chiudi' })[0]); });
    await answerConfirm(true, { confirmText: 'Chiudi senza salvare' });
    expect(onClose2).toHaveBeenCalledTimes(1);
  });

  it('N7: una selezione che parte da un campo e finisce sull\'overlay non chiude', () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const overlay = document.querySelector('.wizard__overlay');
    fireEvent.mouseDown(screen.getByText('tipo'));
    fireEvent.click(overlay);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('footer proprio: Annulla passa da requestClose (stessa conferma di × ed Esc)', async () => {
    const onClose = vi.fn();
    render(
      <Wizard
        isOpen onClose={onClose} title="W" step={0} onStepChange={() => {}}
        steps={[{ label: 'File', content: <input aria-label="file" onChange={() => {}} /> }]}
        footer={({ requestClose }) => <button type="button" onClick={requestClose}>Annulla</button>}
      />,
    );
    fireEvent.change(screen.getByLabelText('file'), { target: { value: 'x' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Annulla' })); });
    expect(onClose).not.toHaveBeenCalled();
    await answerConfirm(true, { confirmText: 'Chiudi senza salvare' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('ogni wizard dell\'app usa le regole del Wizard (enumerazione)', () => {
  const SRC = path.resolve(import.meta.dirname, '../../src');
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : (/\.jsx$/.test(e.name) ? [full] : []);
  });
  const users = walk(SRC)
    .filter(f => !f.endsWith(`common${path.sep}Wizard.jsx`))
    .map(f => ({ file: path.relative(SRC, f).split(path.sep).join('/'), code: fs.readFileSync(f, 'utf-8') }))
    .filter(({ code }) => /<Wizard\s/.test(code));

  it('trova i wizard (connettori API, Data Source, schedule, upload)', () => {
    expect(users.map(u => u.file).sort()).toEqual([
      'components/api-conn/ApiConnectionWizard.jsx',
      'components/arricchisci/DataSourceConfigModal.jsx',
      'components/modals/ScheduleWizard.jsx',
      'pages/FileManager/UploadModal.jsx',
    ]);
  });

  it('niente navigazione o invio fuori dal Wizard: onComplete o footer come funzione con requestClose', () => {
    const offenders = [];
    for (const { file, code } of users) {
      const usesComplete = /onComplete=\{/.test(code);
      const fnFooter = /const footer = \(\{ requestClose \}\) =>/.test(code) && /onClick=\{requestClose\}/.test(code);
      if (!usesComplete && !fnFooter) offenders.push(`${file}: ne' onComplete ne' footer con requestClose`);
      if (/footer=\{(?!footer\})/.test(code)) offenders.push(`${file}: footer non dichiarato come funzione`);
    }
    expect(offenders).toEqual([]);
  });
});
