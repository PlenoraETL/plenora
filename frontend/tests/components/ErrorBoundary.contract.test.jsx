/**
 * T3 / R5.7 — Error Boundary per area.
 *
 *  - dentro il Router un crash di pagina sostituisce solo la pagina, con
 *    "Ricarica" e "Torna alla home", e si azzera cambiando rotta;
 *  - ogni boundary invia l'errore a Sentry se e' configurato;
 *  - i testi sono italiani (l'eccezione grezza solo in sviluppo).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, Link } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AppErrorBoundary,
  RouteErrorBoundary,
  CanvasErrorBoundary,
  ModalErrorBoundary,
} from '../../src/components/common/ErrorBoundary';
import { setErrorReporter } from '../../src/utils/errorReporting';

function Boom() {
  throw new Error('kaboom interno');
}

describe('Error Boundary per area (R5.7)', () => {
  let captureException;
  let consoleError;
  // React in sviluppo rilancia l'errore catturato come evento `error` della
  // finestra: lo si silenzia per non sporcare l'output.
  const swallow = (e) => e.preventDefault();

  beforeEach(() => {
    captureException = vi.fn();
    setErrorReporter({ captureException });
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    window.addEventListener('error', swallow);
  });

  afterEach(() => {
    setErrorReporter(null);
    consoleError.mockRestore();
    window.removeEventListener('error', swallow);
  });

  it('un crash di pagina resta nella pagina, Torna alla home cambia rotta e azzera il boundary', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/rotta-rotta']}>
        <nav><Link to="/">Fuori</Link></nav>
        <RouteErrorBoundary>
          <Routes>
            <Route path="/" element={<div>Home ok</div>} />
            <Route path="/rotta-rotta" element={<Boom />} />
          </Routes>
        </RouteErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByText('Questa pagina ha incontrato un errore')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ricarica' })).toBeInTheDocument();
    // La navigazione fuori dal boundary resta.
    expect(screen.getByRole('link', { name: 'Fuori' })).toBeInTheDocument();
    expect(captureException).toHaveBeenCalledTimes(1);
    expect(captureException.mock.calls[0][0].message).toBe('kaboom interno');
    expect(captureException.mock.calls[0][1].tags).toEqual({ error_boundary: 'pagina' });

    await user.click(screen.getByRole('button', { name: 'Torna alla home' }));
    expect(screen.getByText('Home ok')).toBeInTheDocument();
  });

  it.each([
    ['app', AppErrorBoundary, 'Plenora — Errore'],
    ['canvas', CanvasErrorBoundary, 'Errore nel canvas'],
    ['pannello', ModalErrorBoundary, 'Si è verificato un errore imprevisto nel pannello.'],
  ])('area %s: testo italiano e invio a Sentry', (area, Boundary, text) => {
    render(<MemoryRouter><Boundary><Boom /></Boundary></MemoryRouter>);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(captureException).toHaveBeenCalledTimes(1);
    expect(captureException.mock.calls[0][1].tags).toEqual({ error_boundary: area });
  });

  it('senza Sentry configurato il boundary funziona lo stesso', () => {
    setErrorReporter(null);
    render(<AppErrorBoundary><Boom /></AppErrorBoundary>);
    expect(screen.getByRole('button', { name: 'Torna alla home' })).toBeInTheDocument();
    expect(captureException).not.toHaveBeenCalled();
  });
});
