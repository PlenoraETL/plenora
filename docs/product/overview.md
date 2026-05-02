# Product overview — URBY-ETL

ETL platform per pipeline di dati geo-spaziali e tabulari. Canvas-based
no-code/low-code: l'utente compone pipeline collegando nodi tipizzati
(source → transform → sink) in un editor ReactFlow.

> **Status**: Phase 1 hardening + structural closure completata
> 2026-05-02. Production endpoint `lab.plenora.it`
> (15.160.219.93:5004). FE e BE sono in zero-residuals operativo;
> i rimanenti FE item sono decision gate Phase 2 documentati.

---

## Current capabilities

Tutti i moduli sotto sono **LIVE** in produzione.

### Pipeline visuale (canvas-based)

| Modulo | Cosa fa |
|---|---|
| **Estrai** | Query SQL multi-dialect (PostgreSQL, MySQL, Oracle, MSSQL, SQLite) + ArcGIS Feature Service. Output a file (csv/json/xlsx/geojson/gpkg/shp/parquet). |
| **Manipola** | 33+ trasformazioni dati (filter, join, multi-join, pivot, aggregate, dedup, geo ops: clip / dissolve / overlay / sjoin / nearest, ecc.). |
| **Arricchisci** | Pipeline arricchimento via REST API esterne (auth: token / OAuth2 / public / custom), REST File processor, executable command-based. Parametri statici, mapping risposta JSON, parallelizzazione nodi indipendenti. |
| **Carica** | DB writer (replace atomic / append / upsert / Oracle redef) + ArcGIS Feature Service publish (replace / append / update / upsert). |
| **IMDF** | Apple Indoor Mapping Data Format: bundle generation + ArcGIS publish con renderers configurabili. |

### Operatività

| Componente | Cosa fa |
|---|---|
| **Scheduler** | Cron + Celery beat con leader election (Redis SETNX), heartbeat, catchup tracking, log archive. |
| **Eseguibili pubblicati** | Pipeline serializzate eseguibili da non-admin (ruolo `esecutore` kiosk-only) con upload temporaneo. |
| **Auth** | JWT HS256 (8h, no refresh), MFA TOTP opt-in, 5-role RBAC (`superadmin` / `admin` / `user` / `viewer` / `esecutore`), group-based tenant isolation. |
| **Audit & observability** | Run/log persistence in PG, intermediate snapshot mode (`metadata_only` / `preview_only` / `full_pg` / `full_artifact`) con per-call override superadmin. |
| **Intermediate persistence** | Object storage backend (AWS S3 / MinIO) per Parquet artifact + manifest, opt-in via mode. |

---

## Posizionamento

URBY-ETL e' un prodotto **interno** Plenora Holding, non distribuito,
non multi-tenant SaaS. Use case principali:

- **Integrazione DB → ArcGIS Feature Service**: ETL ricorrenti per
  alimentare dashboard/mappe con dati geo-spaziali.
- **Arricchimento dati via API esterne**: pipeline che fanno query a
  servizi (geocoding, lookup catalogo, business data) riga-per-riga
  o batch.
- **Indoor mapping (IMDF)**: bundle Apple IMDF da CAD/DXF, publish su
  ArcGIS con renderers configurabili.
- **Pipeline schedulate**: cron-based con catchup tracking + run
  history navigabile.

---

## Out of scope

Direzioni **non implementate**, esposte come roadmap pura
(vedi [roadmap.md](roadmap.md) §7):

- Capability services / AI assist.
- Streaming ingestion (Kafka / Pulsar).
- GIS satellitare avanzato (raster, NDVI, zonal stats).
- Multi-tenant SaaS hardening.
- Data catalog cross-progetto con lineage.

Trigger per aprire questi servizi: **richiesta business concreta**.

---

## Riferimenti tecnici

- [Architecture overview](../architecture/overview.md) — core platform,
  BE/FE boundaries, execution flow.
- [Roadmap](roadmap.md) — residui audit, Phase 2/3/4+, capability
  services come direzione futura.
- [Decisioni architetturali](../architecture/decisions/) — ADR
  registry formale (Nygard, append-only).
- [Backend setup](../development/backend.md) · [Frontend setup](../development/frontend.md).
