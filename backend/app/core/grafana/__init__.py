"""Dashboard Grafana degli Eseguibili (11/11/2026).

Modulo FOGLIA: non importa nessun dominio di ``app.core`` (solo ``app.config``,
``app.environment``, ``app.shared``, ``app.utils``), cosi' ``eseguibili ->
grafana`` e ``auth -> grafana`` non creano cicli nel grafo dei domini.

- ``settings``: configurazione (DEFAULTS) e segreti (.env);
- ``signing``: lasciapassare dei dati e cookie di sessione, firmati con
  ``DASHBOARD_PASS_SECRET`` (mai ``JWT_SECRET_KEY``), domini separati;
- ``client``: API HTTP di Grafana (finto nei test: ``set_client_factory``);
- ``dashboard_builder``: dashboard di partenza e colonne referenziate;
- ``sync``: cartelle, team e ruoli, idempotente.

Il distinto ``app/core/dashboard/`` e' il grafo della home, non c'entra.
"""
