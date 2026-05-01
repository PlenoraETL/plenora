/**
 * Contract test — templateVariables (FE-NEXT-1, FE-T1).
 *
 * Pure function. Resolver di {{NOW}}, {{TODAY}}, {{NOW+2h}}, ecc.
 * Usato da StaticValuesNode + scheduler. Bug silente = chiamate API
 * con variabili non sostituite (es. URL con `{{NOW}}` letterale).
 *
 * Strategia: usiamo `referenceDate` per rendere i test deterministici
 * (la funzione accetta esplicitamente un `referenceDate`, no need per
 * vi.useFakeTimers() o monkey-patching).
 */

import { describe, it, expect } from 'vitest';
import {
  processTemplateVariables,
  hasTemplateVariables,
  extractTemplateVariables,
  AVAILABLE_VARIABLES,
} from '../../src/utils/templateVariables';

// Reference date fissa per i test: 2026-03-15 14:30:45.
// Mese 2 (0-based) = marzo. Giorno 15. Ora 14:30:45.
const REF = new Date(2026, 2, 15, 14, 30, 45);


describe('processTemplateVariables', () => {

  describe('input edge cases', () => {
    it('null -> null', () => {
      expect(processTemplateVariables(null)).toBeNull();
    });

    it('undefined -> undefined', () => {
      expect(processTemplateVariables(undefined)).toBeUndefined();
    });

    it('stringa vuota -> stringa vuota', () => {
      expect(processTemplateVariables('')).toBe('');
    });

    it('non-string -> ritorna invariato', () => {
      expect(processTemplateVariables(42)).toBe(42);
      expect(processTemplateVariables({ a: 1 })).toEqual({ a: 1 });
    });

    it('stringa senza template -> invariata', () => {
      expect(processTemplateVariables('plain text', REF)).toBe('plain text');
    });
  });

  describe('variabili base', () => {
    it('{{NOW}} -> YYYY-MM-DD HH:mm', () => {
      expect(processTemplateVariables('{{NOW}}', REF)).toBe('2026-03-15 14:30');
    });

    it('{{TODAY}} -> YYYY-MM-DD 00:00', () => {
      expect(processTemplateVariables('{{TODAY}}', REF)).toBe('2026-03-15 00:00');
    });

    it('{{YESTERDAY}} -> ieri 00:00', () => {
      expect(processTemplateVariables('{{YESTERDAY}}', REF)).toBe('2026-03-14 00:00');
    });

    it('{{TOMORROW}} -> domani 00:00', () => {
      expect(processTemplateVariables('{{TOMORROW}}', REF)).toBe('2026-03-16 00:00');
    });

    it('{{DATE}} -> solo YYYY-MM-DD', () => {
      expect(processTemplateVariables('{{DATE}}', REF)).toBe('2026-03-15');
    });

    it('{{TIME}} -> HH:mm', () => {
      expect(processTemplateVariables('{{TIME}}', REF)).toBe('14:30');
    });

    it('{{TIMESTAMP}} -> ms unix', () => {
      const result = processTemplateVariables('{{TIMESTAMP}}', REF);
      expect(result).toBe(String(REF.getTime()));
    });

    it('{{YEAR}} / {{MONTH}} / {{DAY}}', () => {
      expect(processTemplateVariables('{{YEAR}}', REF)).toBe('2026');
      expect(processTemplateVariables('{{MONTH}}', REF)).toBe('03');
      expect(processTemplateVariables('{{DAY}}', REF)).toBe('15');
    });
  });

  describe('offset arithmetic', () => {
    it('{{NOW+2h}} -> +2 ore', () => {
      expect(processTemplateVariables('{{NOW+2h}}', REF)).toBe('2026-03-15 16:30');
    });

    it('{{NOW-30m}} -> -30 minuti', () => {
      expect(processTemplateVariables('{{NOW-30m}}', REF)).toBe('2026-03-15 14:00');
    });

    it('{{NOW-1d}} -> ieri stessa ora', () => {
      expect(processTemplateVariables('{{NOW-1d}}', REF)).toBe('2026-03-14 14:30');
    });

    it('{{NOW+90m}} -> +1.5h via minuti', () => {
      expect(processTemplateVariables('{{NOW+90m}}', REF)).toBe('2026-03-15 16:00');
    });

    it('offset secondi non altera HH:mm visualizzata se piccolo', () => {
      // +30s su 14:30:45 = 14:31:15 -> HH:mm = 14:31
      expect(processTemplateVariables('{{NOW+30s}}', REF)).toBe('2026-03-15 14:31');
    });
  });

  describe('rollover di data/mese', () => {
    it('{{NOW+10h}} oltre midnight -> giorno successivo', () => {
      const lateNight = new Date(2026, 2, 15, 23, 30);
      expect(processTemplateVariables('{{NOW+2h}}', lateNight)).toBe('2026-03-16 01:30');
    });

    it('{{TOMORROW}} a fine mese -> primo giorno del mese successivo', () => {
      const lastDay = new Date(2026, 2, 31, 10, 0); // 31 marzo
      expect(processTemplateVariables('{{TOMORROW}}', lastDay)).toBe('2026-04-01 00:00');
    });
  });

  describe('multiple variables in una stringa', () => {
    it('"da {{TODAY}} a {{TOMORROW}}" -> entrambe sostituite', () => {
      expect(
        processTemplateVariables('da {{TODAY}} a {{TOMORROW}}', REF),
      ).toBe('da 2026-03-15 00:00 a 2026-03-16 00:00');
    });

    it('URL-style con TIMESTAMP -> sostituzione corretta', () => {
      const out = processTemplateVariables(
        'https://api.example.com/data?ts={{TIMESTAMP}}&date={{DATE}}',
        REF,
      );
      expect(out).toBe(`https://api.example.com/data?ts=${REF.getTime()}&date=2026-03-15`);
    });
  });

  describe('variabili sconosciute / malformate', () => {
    it('{{UNKNOWN}} (A-Z ma non riconosciuta) -> default == NOW formatDateTime', () => {
      // Comportamento esistente: la regex matcha qualunque [A-Z]+, e
      // getBaseDate/formatValue su nome non riconosciuto ricadono entrambe
      // nel ramo default (now + formatDateTime). Test "behavior lock"
      // perché un cambio futuro (es. lasciare letterale) sarebbe un
      // breaking change per template legacy che si appoggiano al fallback.
      expect(processTemplateVariables('{{UNKNOWN}}', REF)).toBe('2026-03-15 14:30');
    });

    it('{{notuppercase}} non matcha (regex richiede A-Z) -> letterale', () => {
      expect(processTemplateVariables('{{notuppercase}}', REF)).toBe('{{notuppercase}}');
    });

    it('singola graffa "{NOW}" non matcha -> letterale', () => {
      expect(processTemplateVariables('{NOW}', REF)).toBe('{NOW}');
    });
  });
});


describe('hasTemplateVariables', () => {
  it('null/undefined/non-string -> false', () => {
    expect(hasTemplateVariables(null)).toBe(false);
    expect(hasTemplateVariables(undefined)).toBe(false);
    expect(hasTemplateVariables(42)).toBe(false);
  });

  it('stringa senza template -> false', () => {
    expect(hasTemplateVariables('plain text')).toBe(false);
  });

  it('stringa con {{NOW}} -> true', () => {
    expect(hasTemplateVariables('hello {{NOW}}')).toBe(true);
  });

  it('chiamate consecutive: lastIndex resettato (no false negative)', () => {
    // Se la regex globale non resettasse lastIndex, la 2a call su input
    // diverso fallirebbe. Test garantisce che resettiamo correttamente.
    expect(hasTemplateVariables('first {{TODAY}}')).toBe(true);
    expect(hasTemplateVariables('{{NOW}}')).toBe(true);
    expect(hasTemplateVariables('{{TIMESTAMP}}')).toBe(true);
  });
});


describe('extractTemplateVariables', () => {
  it('null -> []', () => {
    expect(extractTemplateVariables(null)).toEqual([]);
  });

  it('senza template -> []', () => {
    expect(extractTemplateVariables('plain')).toEqual([]);
  });

  it('una variabile semplice', () => {
    expect(extractTemplateVariables('{{NOW}}')).toEqual([
      { match: '{{NOW}}', variable: 'NOW', offset: null },
    ]);
  });

  it('variabile con offset', () => {
    expect(extractTemplateVariables('{{NOW+2h}}')).toEqual([
      { match: '{{NOW+2h}}', variable: 'NOW', offset: '+2h' },
    ]);
  });

  it('multiple variabili nello stesso testo', () => {
    const out = extractTemplateVariables('da {{TODAY}} a {{NOW+1d}}');
    expect(out).toHaveLength(2);
    expect(out[0].variable).toBe('TODAY');
    expect(out[1].variable).toBe('NOW');
    expect(out[1].offset).toBe('+1d');
  });
});


describe('AVAILABLE_VARIABLES (autocomplete table)', () => {
  it('non vuoto + ogni entry ha name+description+example', () => {
    expect(AVAILABLE_VARIABLES.length).toBeGreaterThan(5);
    AVAILABLE_VARIABLES.forEach(v => {
      expect(typeof v.name).toBe('string');
      expect(v.name).toMatch(/^\{\{[A-Z]+/);
      expect(typeof v.description).toBe('string');
      expect(typeof v.example).toBe('string');
    });
  });

  it('contiene almeno NOW + TODAY + TIMESTAMP (variabili core)', () => {
    const names = AVAILABLE_VARIABLES.map(v => v.name);
    expect(names).toContain('{{NOW}}');
    expect(names).toContain('{{TODAY}}');
    expect(names).toContain('{{TIMESTAMP}}');
  });
});
