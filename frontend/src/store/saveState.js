/**
 * UNICA definizione di "modifiche non salvate" (Fase 4, M7): modifiche
 * dell'utente (isDirty). Le correzioni automatiche (pendingSync) si salvano
 * col prossimo salvataggio ma non sono "modifiche": niente avviso. La usano
 * indicatore, avviso di uscita, copia di sicurezza, anteprime e versioni.
 */
export const hasUnsavedChanges = (s) => Boolean(s.isDirty) && !s.isReadOnly;
