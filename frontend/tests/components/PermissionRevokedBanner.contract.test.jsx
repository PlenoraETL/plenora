/**
 * Contract tests for PermissionRevokedBanner.
 *
 * Invariante: quando il task ritorna `result.status='permission_revoked'`,
 * il banner viene mostrato inline (non toast, non fullscreen) col
 * messaggio dal backend, oppure col fallback generico se message vuoto.
 *
 * Il pattern visuale e' coerente con `ImdfPublisherNode.jsx:278-298`
 * (inline-style su <div role='alert'>): niente CSS file dedicato.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import PermissionRevokedBanner from '../../src/components/common/PermissionRevokedBanner';

describe('PermissionRevokedBanner', () => {
  it('renderizza titolo "Permessi revocati" e role=alert', () => {
    render(<PermissionRevokedBanner />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/Permessi revocati/i)).toBeInTheDocument();
  });

  it('mostra il messaggio passato come prop', () => {
    render(<PermissionRevokedBanner message="Ruolo declassato a viewer" />);
    expect(screen.getByText('Ruolo declassato a viewer')).toBeInTheDocument();
  });

  it('mostra messaggio fallback quando message e\' assente', () => {
    render(<PermissionRevokedBanner />);
    // Il fallback contiene la frase chiave "interrotta in modo sicuro"
    expect(
      screen.getByText(/interrotta in modo sicuro/i)
    ).toBeInTheDocument();
  });

  it('mostra messaggio fallback quando message e\' stringa vuota', () => {
    render(<PermissionRevokedBanner message="" />);
    expect(screen.getByText(/interrotta in modo sicuro/i)).toBeInTheDocument();
  });
});
