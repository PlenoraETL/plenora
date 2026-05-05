"""Pure no-op cleanroom smoke task.

BE-GO-READY-INSTALLATION-019-CELERY-WORKER-ENQUEUE-SMOKE-IMPL.

Sole purpose: provide a deterministic completion target for the
Celery worker enqueue/dequeue smoke step in
`.github/workflows/ci-cleanroom.yml`. The task is registered with
the canonical Celery name ``cleanroom.ping`` and returns the
literal string ``"pong"`` so the CI smoke can assert end-to-end
task transit via ``AsyncResult.get(timeout=N)`` against the
existing Redis result backend.

Design contract (locked by
``backend/tests2/shared/test_celery_enqueue_smoke_design_guard.py``):

  - pure no-op: NO imports of ``app.*``, ``requests``, ``boto3``,
    ``open(``, ``Path(``, or ``os.environ``. Even an indirect DB
    or filesystem touch defeats the determinism guarantee;
  - ``ignore_result=False``: the result MUST round-trip through
    the Redis backend so the smoke can observe completion. An
    ``ignore_result=True`` would silently disable the assertion;
  - ``name="cleanroom.ping"``: stable, namespaced; the workflow
    step sends to this exact name;
  - single symbol exported: only the task itself, no helpers, no
    constants, no transitive imports.

The task does NOT exist in production deploys to do anything
other than ship the registration. ``plenora-celery.service`` will
load it (zero behavioural cost) and operators can enqueue
``cleanroom.ping`` by hand via ``celery -A workers.celery_app
call cleanroom.ping`` for diagnostics if needed.
"""

from workers.celery_app import celery_app


@celery_app.task(name="cleanroom.ping", ignore_result=False, bind=False)
def cleanroom_ping() -> str:
    """Return the canonical "pong" string. Pure no-op."""
    return "pong"
