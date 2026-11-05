/**
 * Guida d'uso dell'input «Utente che esegue» (G).
 *
 *  1. UNA fonte: il file del repository docs/guide/input-utente-che-esegue.md
 *     e' la guida dell'app (src/utils/guides/utenteCheEsegue) resa in
 *     Markdown; si rigenera con UPDATE_GUIDES=1.
 *  2. La guida dice cio' che il sistema fa: esempio collaudato (E0041 ->
 *     004_IS con la formula mostrata), regole del backend (valore solo dal
 *     server, prova dal canvas salvato, Pianificazioni e catene bloccate) e
 *     ogni argomento richiesto (sicurezza, configurazione, prova,
 *     pubblicazione, Schedule, errori comuni).
 *  3. Nell'app: scelto il tipo, il nodo «Input di progetto» mostra «Come si
 *     usa» (due modi, esempio, avvertenza) e apre la guida completa, anche
 *     in sola lettura. La guida e' linkata dall'indice dei documenti.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('../../src/hooks/useTransformRegistry', () => ({ useTransformRegistry: () => ({ data: null, status: 'loading' }) }));

import { IDENTITY_EXAMPLE, UTENTE_CHE_ESEGUE_GUIDE } from '../../src/utils/guides/utenteCheEsegue';
import { guideToMarkdown } from '../helpers/guideMarkdown';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import useCanvasStore from '../../src/store/useCanvasStore';

const REPO = path.resolve(import.meta.dirname, '../../..');
const DOC = path.join(REPO, 'docs', 'guide', `${UTENTE_CHE_ESEGUE_GUIDE.slug}.md`);
const SOURCE = 'frontend/src/utils/guides/utenteCheEsegue.js';
const BACKEND_IDENTITY = fs.readFileSync(path.join(REPO, 'backend/app/shared/execution/runtime_identity.py'), 'utf-8');

afterEach(() => { cleanup(); });

const text = (guide) => [guide.intro, ...guide.sections.flatMap(s => [s.title, ...s.blocks.flatMap(b =>
  [b.p, b.note, b.code, ...(b.ul || []), ...(b.ol || [])])])].filter(Boolean).join('\n');

describe('1. one source for the app and the repository', () => {
  it('docs/guide is the in-app guide rendered as Markdown', () => {
    const expected = guideToMarkdown(UTENTE_CHE_ESEGUE_GUIDE, SOURCE);
    if (process.env.UPDATE_GUIDES) {
      fs.mkdirSync(path.dirname(DOC), { recursive: true });
      fs.writeFileSync(DOC, expected, 'utf-8');
    }
    expect(fs.readFileSync(DOC, 'utf-8').replace(/\r\n/g, '\n')).toBe(expected);
  });

  it('the documentation index links the guide', () => {
    const index = fs.readFileSync(path.join(REPO, 'docs', 'README.md'), 'utf-8');
    expect(index).toContain(`guide/${UTENTE_CHE_ESEGUE_GUIDE.slug}.md`);
  });
});

describe('2. the guide says what the system does', () => {
  it('the worked example is right (E0041 -> 004_IS)', () => {
    const { formula, username, value } = IDENTITY_EXAMPLE;
    // slice(1, 4) del nome utente + '_IS', come la Formula di Manipola.
    expect(formula).toBe("utente.str.slice(1, 4) + '_IS'");
    expect(`${username.slice(1, 4)}_IS`).toBe(value);
    const all = text(UTENTE_CHE_ESEGUE_GUIDE);
    [formula, username, value, 'NNN_IS', 'Join interno'].forEach(word => expect(all).toContain(word));
  });

  it('covers every topic asked for', () => {
    expect(UTENTE_CHE_ESEGUE_GUIDE.sections.map(s => s.title)).toEqual([
      "Cos'è",
      'Sicurezza: nessuno può falsificarlo',
      'Configurazione passo passo (esempio)',
      "Provare dall'editor: Anteprima, Valida, Esegui di prova",
      'Pubblicare come Eseguibile',
      'Pianificazioni e catene',
      'Errori comuni',
    ]);
    const all = text(UTENTE_CHE_ESEGUE_GUIDE);
    [/Maiuscole/, /forma diversa dai dati/, /vede l'Eseguibile ma non ha dati/, /solo chi può modificare il progetto/i,
      /nome utente di prova SALVATO/, /non avvia Carica/, /Esecuzione di prova \(utente: …\)/].forEach(re => expect(all).toMatch(re));
  });

  it('matches the backend rules it describes', () => {
    // Valore solo dal server, prova dal canvas salvato, nessun Carica, Schedule e catene bloccate.
    ['il valore lo inietta SOLO il server', 'canvas SALVATO', 'nessun Carica', 'Schedule e catene non passano mai di qui']
      .forEach(rule => expect(BACKEND_IDENTITY).toContain(rule));
    expect(BACKEND_IDENTITY).toContain('runtime_identity_client_value');
  });
});

describe('3. in the app', () => {
  const renderNode = (readOnly = false) => {
    const st = useCanvasStore.getState();
    st.reset();
    st.init('p1', 'manipola');
    st.loadGraph([{ id: 'u', type: 'project_input', position: { x: 0, y: 0 }, data: { input_kind: 'current_user', input_key: 'utente', label: 'Utente' } }], []);
    st.setReadOnly(readOnly);
    render(<ProjectInputNode id="u" data={{ input_kind: 'current_user', input_key: 'utente', label: 'Utente' }} />);
  };

  it.each([false, true])('«Come si usa» and the full guide (read-only: %s)', (readOnly) => {
    renderNode(readOnly);
    const help = screen.getByRole('note', { name: 'Come si usa' });
    expect(help).toHaveTextContent(/Collegato a un parametro/);
    expect(help).toHaveTextContent(/Sorgente/);
    expect(help).toHaveTextContent(IDENTITY_EXAMPLE.formula);
    expect(help).toHaveTextContent(/E0041.*004_IS/);
    expect(help).toHaveTextContent(/esattamente la forma dei dati/);
    const open = within(help).getByRole('button', { name: /Guida completa/ });
    expect(open.disabled).toBe(false);
    fireEvent.click(open);
    const guide = screen.getByRole('article', { name: /Guida: Input «Utente che esegue»/ });
    UTENTE_CHE_ESEGUE_GUIDE.sections.forEach(s => expect(within(guide).getByRole('heading', { name: s.title })).toBeInTheDocument());
    expect(within(guide).getAllByText(IDENTITY_EXAMPLE.formula).length).toBeGreaterThan(0);
  });
});
