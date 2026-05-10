# DR drill log

Registro append-only delle esecuzioni del DR drill. Ogni entry è
prodotta seguendo il template in [`dr_drill_playbook.md §5`](dr_drill_playbook.md).

**Cadenza target**: trimestrale.

**Convenzione**:
- Entry più recente in alto.
- Nessuna entry viene modificata dopo la chiusura del drill (append-only).
- Eventuali correzioni si fanno con un'entry "errata corrige" successiva
  che cita la precedente.

**Convenzione dei target dichiarati al momento di questa creazione**:
- RPO target: ≤ 24h
- RTO target: ≤ 2h

Vedi [`dr_drill_playbook.md §4`](dr_drill_playbook.md) per il razionale.

---

## Esecuzioni

_(nessun drill eseguito ancora — la prima entry sarà aggiunta dopo la
prima esecuzione del playbook. Vedi `dr_drill_playbook.md` per i passi.)_
