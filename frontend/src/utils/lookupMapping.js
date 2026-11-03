/**
 * lookupMapping — regole condivise della "Mappa valori" (Lookup), lato
 * frontend: lettura della mappatura in ogni formato salvato e numeri
 * riconosciuti come li riconosce Python (backend shared/execution/lookup_mapping.py).
 */

/** Come Python float(): anche "1_000", "1e3", "inf", "nan". */
export const isPythonFloat = (v) => {
  const s = String(v ?? '').trim();
  return /^[+-]?((\d(_?\d)*)(\.(\d(_?\d)*)?)?|\.\d(_?\d)*)([eE][+-]?\d(_?\d)*)?$/.test(s)
    || /^[+-]?(inf|infinity|nan)$/i.test(s);
};

/** Mappatura Lookup in qualsiasi formato salvato -> righe {from, to}. */
export function readValueMapping(value) {
  if (Array.isArray(value)) {
    return value.filter(Boolean).map(r => ({
      from: String(r.from ?? r.old_name ?? ''), to: String(r.to ?? r.new_name ?? ''),
    }));
  }
  if (value && typeof value === 'object') return Object.entries(value).map(([from, to]) => ({ from, to: String(to ?? '') }));
  return [];
}
