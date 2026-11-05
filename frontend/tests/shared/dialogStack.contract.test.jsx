/**
 * Contract di ENUMERAZIONE: ogni finestra del progetto passa dallo stack
 * unico (Fase 5, T4; R6.1, R6.5, R6.6, R6.10, N7, N10, N14).
 *
 *  1. ogni file che disegna una finestra (role="dialog", aria-modal,
 *     overlay/scrim, createPortal) usa useDialog o ModalWrapper/Wizard/
 *     ConfirmDialog;
 *  2. role="dialog"/aria-modal scritti a mano solo in hooks/useDialog.js (le
 *     finestre li prendono da `dialogProps`);
 *  3. "invio in corso" = `busy`, non la coppia closeOnEsc/closeOnOverlay={!x}
 *     (che lasciava la × attiva);
 *  4. ogni listener globale di tastiera applica la regola unica
 *     isShortcutBlocked; nessuno cerca '[role="dialog"]' nel DOM;
 *  5. ModalWrapper, Wizard e ConfirmDialog si registrano davvero nello stack.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import ModalWrapper from '../../src/components/common/ModalWrapper';
import Wizard from '../../src/components/common/Wizard';
import ConfirmDialog from '../../src/components/common/ConfirmDialog';
import { __dialogStackSize } from '../../src/utils/dialogStack';

const SRC = path.resolve(import.meta.dirname, '../../src');

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return files(full);
    return /\.(jsx?|tsx?)$/.test(e.name) ? [full] : [];
  });
}
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
const ALL = files(SRC).map(f => ({ file: rel(f), code: fs.readFileSync(f, 'utf-8') }));

const DIALOG_MARKERS = [
  /role=["']dialog["']/, /role=["']alertdialog["']/, /aria-modal/,
  /className=\{?[`"'][^`"']*(__overlay|-overlay|__scrim)\b/, /createPortal\(/,
];
const USES_STACK = /useDialog\(|<ModalWrapper\b|<Wizard\b|<ConfirmDialog\b/;

// Non sono finestre modali (motivo dichiarato).
const NOT_A_DIALOG = {
  'components/common/LoadingOverlay.jsx': 'spinner di caricamento, non riceve focus ne\' Esc',
  'context/ToastContext.jsx': 'portale dei toast (role="status"), non modale',
  'hooks/useDialog.js': 'lo stack stesso',
};

// Coppia closeOnEsc/closeOnOverlay con semantica diversa da "occupato".
const CLOSE_PAIR_EXCEPTIONS = {
  'components/modals/ExecutionModal.jsx': 'esecuzione asincrona: Esc chiude (la corsa continua), l\'overlay no (tranche T2)',
  'components/eseguibili/ExecutableDynamicRunForm.jsx': 'polling senza onError (N12, tranche T2): la × deve restare usabile finche\' T2 non chiude N12',
};

afterEach(() => cleanup());

describe('stack unico delle finestre — enumerazione', () => {
  it('1. ogni file con una finestra usa lo stack', () => {
    const offenders = ALL
      .filter(({ file, code }) => !NOT_A_DIALOG[file] && DIALOG_MARKERS.some(re => re.test(code)) && !USES_STACK.test(code))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('2. role="dialog" e aria-modal solo da dialogProps (hooks/useDialog)', () => {
    const offenders = ALL
      .filter(({ file, code }) => file !== 'hooks/useDialog.js'
        && /role=["'](alert)?dialog["']|aria-modal=/.test(code))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('3. "invio in corso" si dichiara con busy (× compresa)', () => {
    const offenders = ALL
      .filter(({ file, code }) => !CLOSE_PAIR_EXCEPTIONS[file] && /closeOn(Esc|Overlay)=\{\s*!/.test(code))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('4. ogni listener globale di tastiera applica la regola unica', () => {
    const listeners = ALL.filter(({ file, code }) => file !== 'utils/dialogStack.js'
      && /addEventListener\(\s*['"]keydown['"]/.test(code));
    expect(listeners.length).toBeGreaterThanOrEqual(4);
    const offenders = listeners.filter(({ code }) => !/isShortcutBlocked\(/.test(code)).map(({ file }) => file);
    expect(offenders).toEqual([]);
    const domChecks = ALL.filter(({ code }) => /querySelector\(\s*['"]\[role="dialog"\]/.test(code)).map(({ file }) => file);
    expect(domChecks).toEqual([]);
  });

  it('5. ModalWrapper, Wizard e ConfirmDialog si registrano nello stack', () => {
    const cases = [
      <ModalWrapper key="m" isOpen onClose={vi.fn()} title="M">x</ModalWrapper>,
      <Wizard key="w" isOpen onClose={vi.fn()} title="W" step={0} onStepChange={vi.fn()} steps={[{ label: 'a', content: 'a' }]} />,
      <ConfirmDialog key="c" isOpen config={{ title: 'C' }} handleConfirm={vi.fn()} handleCancel={vi.fn()} />,
    ];
    for (const ui of cases) {
      const { unmount } = render(ui);
      expect(__dialogStackSize()).toBe(1);
      unmount();
      expect(__dialogStackSize()).toBe(0);
    }
  });
});
