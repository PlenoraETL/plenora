# Plenora Phase 0/6 - Second Run + Product Grade

Questa cartella prepara la seconda run del ciclo 0 -> 5.

La run 0 -> 5 e' una nuova iterazione dello stesso metodo, applicata dopo la
bonifica strutturale della Phase 5.

Phase 6 e' separata: non e' refactor generico. E' il gate finale per portare
Plenora a qualita' prodotto 9/10.

Obiettivo:
- ripartire da una codebase piu' modulare;
- verificare le ipotesi emerse nella prima run;
- consolidare, stabilizzare, hardenizzare, auditare, pubblicare e rifinire con
  piu' profondita';
- mantenere il protocollo che ha funzionato: preflight, cluster coesi, stop
  pre-commit, test gate e closeout.

Regola chiave:

> I closeout Phase 5 sono ipotesi operative, non autorita'. Verificare sempre
> con codice, grep, call graph e test.

La seconda run deve sapere cosa e' stato imparato nella prima, ma non deve
limitarsi a confermarlo. Il mandato e':

- usare la mappa esistente per orientarsi;
- sfidare la mappa quando il codice dice altro;
- andare piu' in profondita' sui comportamenti, non solo sulla forma;
- mantenere commit e decisioni piccoli abbastanza da essere reversibili.

Struttura:
- `CONTEXT.md` - mappa iniziale del prodotto e dei domini.
- `METHOD.md` - protocollo operativo della nuova run.
- `LEDGER.md` - debiti/carry-over da validare.
- `phase_0_consolidation/` - seconda consolidation.
- `phase_1_stabilization/` - seconda stabilization.
- `phase_2_hardening/` - seconda hardening.
- `phase_3_audit/` - seconda audit.
- `phase_4_launch/` - remote/deploy/publication readiness.
- `phase_5_structural_quality/` - seconda passata strutturale/qualitativa.
- `phase_6_product_grade/` - readiness prodotto 9/10: deploy, ops,
  security, UX, performance e release certification.
