# Q-PERF-5 — Intermediate run drift detection

`Q-PERF-5` non è una query SQL ma un riferimento allo script Python
esistente che produce un report tabellare per-run di
`intermediate.node_meta` + `COUNT(node_rows)` + `jsonb_bytes`.

## Strumento

`backend/scripts/intermediate_run_report.py`

## Scopo

Drift detection cross-run: confrontare lo stesso project_id /
canvas_id su run consecutive per scoprire variazioni inaspettate
in:
- numero righe persistite (`persisted_rows_count`)
- size in bytes del JSONB (`jsonb_bytes`)
- numero di nodi snapshot-ati


## Esecuzione

Lo script richiede accesso a un PG con lo schema `plenora`:

```sh
cd backend
python scripts/intermediate_run_report.py <run_id>
```

Output: tabella stdout con righe per-nodo, ordinabile per
`persisted_rows_count` o `jsonb_bytes`.

## Quando usarlo

- Post-deploy per verificare che la persistence policy non sia regredita
  (es. da `metadata_only` a `full_pg` involontariamente)
- Cross-week drift detection: stesso run_id pattern, byte volume in
  crescita anomala
- Debug compare modal `mode_mismatch` — capire quale nodo ha cambiato
  modalità snapshot

## Vincoli

- Read-only: lo script fa solo SELECT su `node_meta` + `node_rows`
- Non modifica DB
- Output sicuro per copy-incolla in chat / ticket (niente segreti)

## Out-of-scope

- Aggregazione cross-run automatica (oggetto di A2 quando sink è scelto)
- Allerting su drift (richiede infra alerting non ancora pianificata)
