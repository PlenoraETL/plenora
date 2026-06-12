# Plenora 2.0 — Baseline performance Fase 0 (stack Celery+Redis)

Data: 2026-06-12 · Host: Docker Desktop/WSL2 (Windows 11) · Stack compose single-server
Strumento: `backend/scripts/perf_baseline_stack.py` (rieseguire identico dopo ogni fase)
Dati grezzi: [baseline-celery-redis-2026-06-12.json](baseline-celery-redis-2026-06-12.json)

| Metrica (piano §5) | p50 | p95 | Note |
|---|---|---|---|
| GET /health (controllo, no auth) | 0,80 ms | 0,95 ms | pavimento HTTP+FastAPI |
| **GET /api/auth/me (JWT + blacklist Redis)** | **1,83 ms** | 2,09 ms | **+1,0 ms vs controllo = budget del percorso auth**; il check blacklist è una frazione di quel ms |
| Dispatch task (broker Redis) | 1,59 ms | 44,5 ms | p95 = primo invio a freddo |
| Lettura stato task (result backend Redis) | 0,44 ms | 1,16 ms | il polling dell'orchestratore paga questo |
| Roundtrip task no-op (ping) | 609 ms | 615 ms | dominato da prefetch/poll worker (50 ms step), non dall'infrastruttura: proxy overhead pipeline |
| Login rate-limit (tentativo, sliding window Redis) | 3,83 ms | 32,5 ms | 113,7 tentativi/sec sostenuti; 429 dal tentativo 11 |

## Criteri di accettazione per le fasi successive

- **Fase 2 (result backend → PG)**: lettura stato ≤ ~2 ms p50 (oggi 0,44) resta invisibile nel polling; roundtrip invariato.
- **Fase 4 (blacklist → PG + cache RAM)**: il delta auth per-request deve restare ≈ +1 ms p50 — con cache in-memory davanti il check non tocca PG sul percorso caldo, quindi il target è parità.
- **Fase 4 (rate-limit login)**: soglia 429 identica (semantica), tentativi/sec sostenuti ≥ 50 (oggi 113: ampio margine, il login non è un percorso caldo).
- **Fase 1 (broker → RabbitMQ)**: dispatch ≤ ~5 ms p50.

## Finding d'installazione (per Fase 5)

- `scripts/install-docker.ps1` rotto su Windows PowerShell 5.1 (`RandomNumberGenerator.Fill` è .NET Core/PS7+).
- La network esterna `plenora` va creata a mano al primo install (`docker network create plenora`).
- `install-docker.sh` richiede `python3` sul PATH host per i secret (assente di default su Windows).
