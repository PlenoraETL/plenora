"""Pure no-op cleanroom beat tick smoke task.

BE-GO-READY-INSTALLATION-021-CELERY-BEAT-TICK-SMOKE-IMPL.

Sole purpose: provide a deterministic completion target for the
Celery beat tick smoke step in `.github/workflows/ci-cleanroom.yml`.
The task is registered with the canonical Celery name
``cleanroom.beat_ping`` and returns the literal string ``"beat_pong"``
so the CI smoke can observe end-to-end beat-tick transit by
scanning the Redis result backend (``celery-task-meta-*`` keyspace)
for ``status=SUCCESS`` + ``result="beat_pong"``.

Design contract (locked by Group C of
``backend/tests2/shared/test_celery_beat_tick_smoke_design_guard.py``):

  - pure no-op: NO imports of ``app.*``, ``requests``, ``boto3``,
    ``open(``, ``Path(``, or ``os.environ``. Even an indirect DB
    or filesystem touch defeats the determinism guarantee of the
    smoke (and would make beat-tick failures look like task
    business-logic failures);
  - ``ignore_result=False``: the result MUST round-trip through
    the Redis backend so the smoke can observe completion via
    keyspace scan. ``ignore_result=True`` would silently disable
    the assertion;
  - ``name="cleanroom.beat_ping"``: stable, namespaced; the workflow
    step expects this exact name in the result-backend payload;
  - single symbol exported: only the task itself, no helpers, no
    constants, no transitive imports.

Firing path
-----------
The task is registered at import time (via ``celery_app.task``).
Firing is opt-in: ``backend/workers/celery_app.py`` checks the
env var ``PLENORA_CLEANROOM_BEAT_PING=1`` at import time and, if
set, populates ``celery_app.conf.beat_schedule`` with a 5-second
interval entry pointing at this task. Production deploys leave
the env var unset, so beat NEVER fires this task in production.

The matching ``PlenoraBeatScheduler`` shim in
``backend/workers/beat_scheduler.py`` (``_merge_config_beat_schedule``)
loads config-based ``beat_schedule`` entries into ``self.data``
alongside the DB-driven schedules so the parent ``Scheduler.tick()``
fires them as expected.
"""

from workers.celery_app import celery_app


@celery_app.task(name="cleanroom.beat_ping", ignore_result=False, bind=False)
def cleanroom_beat_ping() -> str:
    """Return the canonical "beat_pong" string. Pure no-op."""
    return "beat_pong"
