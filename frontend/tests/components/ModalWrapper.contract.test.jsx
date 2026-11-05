/**
 * Contract test per ModalWrapper.
 *
 * Invarianti stabili che la migrazione dei modal (C2) deve preservare:
 *   1. Render condizionale via isOpen
 *   2. role="dialog" + aria-modal per accessibilita'
 *   3. Close via onClose: ESC, overlay click, close button
 *   4. closeOnEsc e closeOnOverlay props rispettate
 *   5. loading state mostra spinner invece del children
 *   6. Footer/icon/subtitle opzionali
 *   7. ModalButton: loading + disabled state
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';

import ModalWrapper, { ModalButton } from '../../src/components/common/ModalWrapper';


describe('ModalWrapper', () => {
  it('non renderizza nulla se isOpen=false', () => {
    render(<ModalWrapper isOpen={false} onClose={vi.fn()}>content</ModalWrapper>);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renderizza dialog con role e aria-modal quando isOpen=true', () => {
    render(
      <ModalWrapper isOpen={true} onClose={vi.fn()} title="Hello">
        <p>Inner content</p>
      </ModalWrapper>
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByText('Inner content')).toBeInTheDocument();
  });

  it('mostra titolo, icona e subtitle se forniti', () => {
    render(
      <ModalWrapper
        isOpen={true}
        onClose={vi.fn()}
        title="Il titolo"
        icon="🔑"
        subtitle="sottotitolo qui"
      >
        body
      </ModalWrapper>
    );
    expect(screen.getByText('Il titolo')).toBeInTheDocument();
    expect(screen.getByText('🔑')).toBeInTheDocument();
    expect(screen.getByText('sottotitolo qui')).toBeInTheDocument();
  });

  it('close button chiama onClose', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <ModalWrapper isOpen={true} onClose={onClose} title="X">
        body
      </ModalWrapper>
    );
    await user.click(screen.getByRole('button', { name: /chiudi/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('ESC chiama onClose quando closeOnEsc=true (default)', () => {
    const onClose = vi.fn();
    render(
      <ModalWrapper isOpen={true} onClose={onClose} title="X">
        body
      </ModalWrapper>
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('ESC NON chiama onClose quando closeOnEsc=false', () => {
    const onClose = vi.fn();
    render(
      <ModalWrapper isOpen={true} onClose={onClose} title="X" closeOnEsc={false}>
        body
      </ModalWrapper>
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('mostra loading spinner al posto del children se loading=true', () => {
    render(
      <ModalWrapper isOpen={true} onClose={vi.fn()} title="X" loading loadingText="Attendi...">
        <p>body-non-visibile</p>
      </ModalWrapper>
    );
    expect(screen.getByText('Attendi...')).toBeInTheDocument();
    expect(screen.queryByText('body-non-visibile')).toBeNull();
  });

  it('renderizza il footer se fornito', () => {
    render(
      <ModalWrapper
        isOpen={true}
        onClose={vi.fn()}
        title="X"
        footer={<button type="button">Azione</button>}
      >
        body
      </ModalWrapper>
    );
    expect(screen.getByRole('button', { name: 'Azione' })).toBeInTheDocument();
  });

  it('aria-labelledby punta al titolo quando presente', () => {
    render(
      <ModalWrapper isOpen={true} onClose={vi.fn()} title="Titolino">
        body
      </ModalWrapper>
    );
    const dialog = screen.getByRole('dialog');
    // R6.1: id unico per finestra (stack unico, hooks/useDialog), non 'modal-title'.
    const id = dialog.getAttribute('aria-labelledby');
    expect(id).toBeTruthy();
    expect(id).not.toBe('modal-title');
    // e il titolo ha l'id corrispondente
    expect(screen.getByText('Titolino')).toHaveAttribute('id', id);
  });
});


describe('ModalButton', () => {
  it('renderizza children per variante primary', () => {
    render(<ModalButton variant="primary" onClick={vi.fn()}>Conferma</ModalButton>);
    expect(screen.getByRole('button', { name: 'Conferma' })).toBeInTheDocument();
  });

  it('loading state disabilita + mostra "Attendere..."', () => {
    render(<ModalButton loading onClick={vi.fn()}>Salva</ModalButton>);
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent(/attendere/i);
    expect(btn).not.toHaveTextContent('Salva');
  });

  it('disabled prop disabilita il bottone', () => {
    render(<ModalButton disabled onClick={vi.fn()}>X</ModalButton>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('onClick viene chiamato se non disabled', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<ModalButton onClick={onClick}>X</ModalButton>);
    await user.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
