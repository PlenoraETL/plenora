/**
 * Sessione 2 P0 — ArricchisciEditor availableColumns useEffect deps.
 *
 * Bug originale: l'useEffect che propaga `availableColumns` al
 * ResultNode/CaricaNode aveva deps `[setNodes]` solo. Si attivava
 * SOLO al mount; il cambio di `output_mapping` su un nodo api_enrich
 * (utente che configura il mapping output) NON ri-firrava l'effect →
 * ResultNode esponeva colonne stale.
 *
 * Post-fix: dep aggiuntiva `availableColumnsFingerprint` (memo
 * JSON-derivato dai field rilevanti di nodes). Cambia quando cambia
 * uno dei `output_mapping` → effect rifirera → ResultNode aggiornato.
 *
 * Strategia: lint architetturale. Testare il rendering completo di
 * ArricchisciEditor richiede ReactFlow + Zustand + router provider
 * + ~10 mock — fuori scope per un contract di regressione.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


describe('ArricchisciEditor availableColumns deps lint (sessione 2 P0)', () => {
  const source = readFileSync(
    resolve(__dirname, '../../src/pages/ArricchisciEditor.jsx'),
    'utf-8',
  );

  it('ha un useMemo che calcola availableColumnsFingerprint da nodes', () => {
    expect(source).toContain('availableColumnsFingerprint');
    expect(source).toMatch(/useMemo\(/);
    // Il fingerprint deve essere derivato da `nodes` (dep)
    const memoIdx = source.indexOf('availableColumnsFingerprint = useMemo');
    expect(memoIdx).toBeGreaterThan(-1);
    // Verifica che la chiusura del useMemo abbia [nodes] come dep
    const window = source.slice(memoIdx, memoIdx + 800);
    expect(window).toMatch(/\}\s*,\s*\[nodes\]\)/);
  });

  it('useEffect availableColumns ha fingerprint nelle deps', () => {
    // Cerca il pattern di chiusura dell'effect target
    // `}, [setNodes, availableColumnsFingerprint]);`
    expect(source).toMatch(
      /\}\s*,\s*\[setNodes\s*,\s*availableColumnsFingerprint\]\)/,
    );
  });

  it('fingerprint copre i campi rilevanti: source cols + api_enrich output_mapping', () => {
    const memoIdx = source.indexOf('availableColumnsFingerprint = useMemo');
    expect(memoIdx).toBeGreaterThan(-1);
    const window = source.slice(memoIdx, memoIdx + 800);
    // Deve guardare project_source/file_source columns
    expect(window).toMatch(/project_source|file_source/);
    // E api_enrich output_mapping
    expect(window).toContain('output_mapping');
    // E api_enrich type
    expect(window).toContain('api_enrich');
  });
});
