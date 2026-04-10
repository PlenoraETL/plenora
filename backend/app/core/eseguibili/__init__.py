"""
Modulo ESEGUIBILI — catene arricchisci pubblicate come template on-demand.

Phase 1 — MVP isolato:
- Clone canvas da progetti arricchisci esistenti
- Scanner per estrazione automatica delle colonne attese
- Wizard di esecuzione: upload file → mapping colonne → esecuzione
- Riutilizzo del task Celery execute_enrichment_task (canvas come parametro)

Zero modifiche al modulo arricchisci: il canvas dell'eseguibile è indipendente.
"""
