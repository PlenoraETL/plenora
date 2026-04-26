"""
Sub-package `app.core.imdf.arcgis` — split per cluster del modulo
`app.core.imdf.arcgis_uploader`.

Origine
=======
Introdotto in Phase 5 Batch B4.1 (vedi
`orchestrate/phase_5_b4/preflight.md`). `arcgis_uploader.py` resta il
**facade entry point** unico: importa e re-exporta tutti i simboli
pubblici e privati che erano gia' visibili al call-site (worker,
validator, test contract). Questo package contiene la single-source
canonica delle implementazioni, organizzate per cluster di dominio.

Convenzione facade §6.1 (ratificata in B1):
  - `from app.core.imdf.arcgis_uploader import <symbol>` continua a
    funzionare grazie al re-export in `arcgis_uploader.py`.
  - `monkeypatch.setattr(uploader_mod, "<helper>", ...)` rebinda
    l'attributo nel facade; i chiamanti interni al modulo
    (uploader e validator lazy-import) risolvono via attribute lookup
    sul facade → il fake e' visibile.

Logger naming contract:
  - Tutti i moduli figli usano esplicitamente
    `get_logger("app.core.imdf.arcgis_uploader")` (NOT `__name__`)
    per preservare il nome dei record di log osservato dai consumer.

`__init__.py` lasciato vuoto (no auto-import) per evitare cicli di
import: i singoli moduli figli sono importati esplicitamente da
`arcgis_uploader.py`.
"""
