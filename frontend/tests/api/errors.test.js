/**
 * errors.test.js — Unit test per l'hub handleCascade409 + formatCascadeDependencies.
 *
 * Invariante blindato per error-handling governance:
 *   - handleCascade409 ritorna l'oggetto normalizzato { error, message,
 *     dependencies } solo se lo status e' 409 E il detail ha la shape
 *     canonica (error string non-empty + dependencies array). Null
 *     altrimenti (400/500/network/shape invalida).
 *   - formatCascadeDependencies raggruppa per kind e produce una
 *     stringa user-facing tipo "N eseguibile/i (...) e M progetto/i (...)".
 *
 * Proteggiamo contro:
 *   - reintroduzione di parsing duplicato inline (ProjectList, etc.)
 *   - drift di shape BE (i 4 router devono convergere su { error,
 *     message, dependencies }; se un router tornasse a in_use_by /
 *     projects, l'hub ritornerebbe null e il caller vedrebbe solo un
 *     toast generico — il contract test BE blinda l'altro lato).
 */

import { describe, it, expect } from 'vitest';
import { handleCascade409, formatCascadeDependencies } from '../../src/api/errors';

function fakeError(status, data) {
  return { response: { status, data } };
}

describe('handleCascade409', () => {
  it('riconosce un 409 cascade con shape canonica', () => {
    const err = fakeError(409, {
      detail: {
        error: 'project_has_dependencies',
        message: 'Progetto ha 1 dipendenza',
        dependencies: [
          { kind: 'eseguibile', id: 'ese-1', name: 'Eseguibile A' },
          { kind: 'project', id: 'proj-2', name: 'Progetto B', project_type: 'manipola' },
        ],
      },
    });

    const result = handleCascade409(err);

    expect(result).not.toBeNull();
    expect(result.error).toBe('project_has_dependencies');
    expect(result.message).toBe('Progetto ha 1 dipendenza');
    expect(result.dependencies).toHaveLength(2);
    expect(result.dependencies[0].kind).toBe('eseguibile');
  });

  it('ritorna null se lo status non e\' 409', () => {
    expect(handleCascade409(fakeError(400, { detail: 'bad' }))).toBeNull();
    expect(handleCascade409(fakeError(500, { detail: 'boom' }))).toBeNull();
    expect(handleCascade409(null)).toBeNull();
    expect(handleCascade409(undefined)).toBeNull();
    expect(handleCascade409({})).toBeNull();
  });

  it('ritorna null se detail non e\' un oggetto (es. string FastAPI classico)', () => {
    const err = fakeError(409, { detail: 'generic 409 string' });
    expect(handleCascade409(err)).toBeNull();
  });

  it('ritorna null se manca la chiave error', () => {
    const err = fakeError(409, {
      detail: {
        message: 'La connessione e\' usata in N progetti',
        dependencies: [{ kind: 'project', id: 'p-1', name: 'P1' }],
      },
    });
    expect(handleCascade409(err)).toBeNull();
  });

  it('ritorna null se dependencies non e\' un array', () => {
    const err = fakeError(409, {
      detail: {
        error: 'connection_in_use',
        message: 'usata',
        dependencies: { kind: 'project' },  // dict invece di array → invalido
      },
    });
    expect(handleCascade409(err)).toBeNull();
  });

  it('ritorna null sulla shape legacy in_use_by', () => {
    // Legacy api-connections emitted { message, in_use_by: [...] } without error.
    // L'hub deve rifiutarla: se un router non e' stato migrato, il FE cade
    // sul toast generico invece di mostrare "undefined".
    const err = fakeError(409, {
      detail: {
        message: 'La connessione e\' usata in 1 progetto/i',
        in_use_by: [{ project_id: 'p-1', project_name: 'P1' }],
      },
    });
    expect(handleCascade409(err)).toBeNull();
  });

  it('message vuoto viene normalizzato a stringa vuota', () => {
    const err = fakeError(409, {
      detail: {
        error: 'connection_in_use',
        dependencies: [{ kind: 'project', id: 'p-1', name: 'P1' }],
      },
    });
    const result = handleCascade409(err);
    expect(result).not.toBeNull();
    expect(result.message).toBe('');
  });

  it('preferisce il nuovo envelope details con error/message top-level', () => {
    const err = fakeError(409, {
      error: 'project_has_dependencies',
      message: 'Progetto ha 1 dipendenza',
      details: {
        dependencies: [
          { kind: 'project', id: 'proj-1', name: 'Progetto A' },
        ],
        hidden_count: 1,
      },
    });

    const result = handleCascade409(err);

    expect(result).not.toBeNull();
    expect(result.error).toBe('project_has_dependencies');
    expect(result.message).toBe('Progetto ha 1 dipendenza');
    expect(result.dependencies).toHaveLength(1);
    expect(result.hiddenCount).toBe(1);
  });

  it('usa detail fallback se details esiste ma non contiene dependencies', () => {
    const err = fakeError(409, {
      error: 'connection_in_use',
      message: 'Connessione usata',
      details: { note: 'new envelope without cascade list' },
      detail: {
        error: 'connection_in_use',
        message: 'Connessione usata legacy',
        dependencies: [{ kind: 'project', id: 'p-1', name: 'P1' }],
      },
    });

    const result = handleCascade409(err);

    expect(result).not.toBeNull();
    expect(result.error).toBe('connection_in_use');
    expect(result.message).toBe('Connessione usata');
    expect(result.dependencies).toHaveLength(1);
  });
});

describe('formatCascadeDependencies', () => {
  it('formatta un mix di eseguibili + progetti raggruppati per kind', () => {
    const deps = [
      { kind: 'eseguibile', id: 'e-1', name: 'Alpha' },
      { kind: 'eseguibile', id: 'e-2', name: 'Beta' },
      { kind: 'project', id: 'p-1', name: 'Foo' },
    ];
    const text = formatCascadeDependencies(deps);
    expect(text).toBe('2 eseguibile/i (Alpha, Beta) e 1 progetto/i (Foo)');
  });

  it('formatta solo progetti quando mancano eseguibili', () => {
    const deps = [
      { kind: 'project', id: 'p-1', name: 'Foo' },
      { kind: 'project', id: 'p-2', name: 'Bar' },
    ];
    expect(formatCascadeDependencies(deps)).toBe('2 progetto/i (Foo, Bar)');
  });

  it('ritorna stringa vuota su array vuoto', () => {
    expect(formatCascadeDependencies([])).toBe('');
  });

  it('ritorna stringa vuota su input non-array', () => {
    expect(formatCascadeDependencies(null)).toBe('');
    expect(formatCascadeDependencies(undefined)).toBe('');
    expect(formatCascadeDependencies({})).toBe('');
  });
  it('appende "+ N altro/i non visibili" quando hiddenCount > 0 e progetti visibili', () => {
    const deps = [{ kind: 'project', id: 'p-1', name: 'Mio' }];
    const text = formatCascadeDependencies(deps, 2);
    expect(text).toBe('3 progetto/i (Mio, + 2 altro/i non visibili)');
  });

  it('mostra solo count se hiddenCount > 0 senza progetti visibili', () => {
    const text = formatCascadeDependencies([], 3);
    expect(text).toBe('3 progetto/i (3 non visibili)');
  });

  it('ignora hiddenCount = 0 for backward-compatible responses', () => {
    const deps = [{ kind: 'project', id: 'p-1', name: 'Foo' }];
    expect(formatCascadeDependencies(deps, 0)).toBe('1 progetto/i (Foo)');
    expect(formatCascadeDependencies(deps)).toBe('1 progetto/i (Foo)');
  });

  it('ignora hiddenCount non numerico o negativo', () => {
    const deps = [{ kind: 'project', id: 'p-1', name: 'Foo' }];
    expect(formatCascadeDependencies(deps, -1)).toBe('1 progetto/i (Foo)');
    expect(formatCascadeDependencies(deps, null)).toBe('1 progetto/i (Foo)');
    expect(formatCascadeDependencies(deps, 'invalid')).toBe('1 progetto/i (Foo)');
  });
});

describe('handleCascade409 hidden_count', () => {
  it('estrae hiddenCount dal detail.hidden_count', () => {
    const err = fakeError(409, {
      detail: {
        error: 'datasource_in_use',
        message: 'usato',
        dependencies: [{ kind: 'project', id: 'p-1', name: 'Mio' }],
        hidden_count: 2,
      },
    });
    const result = handleCascade409(err);
    expect(result.hiddenCount).toBe(2);
  });

  it('default hiddenCount = 0 quando hidden_count manca (backward compat)', () => {
    const err = fakeError(409, {
      detail: {
        error: 'connection_in_use',
        message: 'usata',
        dependencies: [{ kind: 'project', id: 'p-1', name: 'P1' }],
      },
    });
    const result = handleCascade409(err);
    expect(result.hiddenCount).toBe(0);
  });

  it('ignora hidden_count negativo o non numerico (default 0)', () => {
    const err = fakeError(409, {
      detail: {
        error: 'connection_in_use',
        message: '',
        dependencies: [],
        hidden_count: -1,
      },
    });
    expect(handleCascade409(err).hiddenCount).toBe(0);
  });
});
