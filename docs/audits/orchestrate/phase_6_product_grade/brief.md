# Phase 6 - Product Grade / 9-10 Readiness Brief

Scopo: portare Plenora da codebase governabile a prodotto affidabile da almeno
9/10.

Phase 6 non e' una nuova fase di refactor tecnico. E' il gate finale di
prodotto, operazioni e rilascio.

Input:
- esiti della run 0 -> 5;
- server/deploy allineati a GitHub pulito;
- baseline security e test aggiornata;
- roadmap residuali classificata.

Focus:
- deploy ripetibile e rollback;
- server allineato a `https://github.com/PlenoraETL/plenora.git`;
- smoke end-to-end post-deploy;
- backup/restore e S3 lifecycle;
- secret scan e security release gate;
- UX dei workflow principali;
- performance su file grandi/job lunghi;
- observability, health, logs e runbook;
- tag release finale.

Out of scope:
- refactor estetici non legati a qualita' prodotto;
- nuove feature non necessarie al release gate;
- cleanup non testabile.
