import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { taskFailureMessage } from '../../src/utils/taskFailureMessage';

/** Contract (collaudo M7): un task fallito mostra il motivo, non "Errore sconosciuto". */
describe('taskFailureMessage', () => {
  it('the readable reason wins over the generic fallback in `error`', () => {
    const task = {
      status: 'failure',
      message: "Formula 'a / b' non valida o non consentita.",
      error: 'Errore sconosciuto',
      result: null,
    };
    expect(taskFailureMessage(task)).toBe("Formula 'a / b' non valida o non consentita.");
  });

  it('order: result detail, result message, task message, error; generic texts skipped', () => {
    expect(taskFailureMessage({ result: { error_message: 'dettaglio' }, message: 'm', error: 'e' })).toBe('dettaglio');
    expect(taskFailureMessage({ result: { message: 'dal risultato' }, message: 'm' })).toBe('dal risultato');
    expect(taskFailureMessage({ message: 'Errore', error: 'codice_x' })).toBe('codice_x');
    expect(taskFailureMessage({ error: 'Errore sconosciuto' }, 'ripiego')).toBe('ripiego');
  });

  it('every hook that reads a failed task goes through the rule', () => {
    const dir = path.resolve(__dirname, '../../src/hooks');
    const offenders = fs.readdirSync(dir).filter(f => /\.js$/.test(f)).filter(f => {
      const src = fs.readFileSync(path.join(dir, f), 'utf-8');
      return /\b(task|data)\.error\s*\|\|/.test(src);
    });
    expect(offenders).toEqual([]);
  });
});
