"""Execution runtime helpers for scheduler runs.

This module owns lock heartbeat compatibility wrappers and failure
finalization so ExecutionManager stays focused on orchestration.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional
from typing_extensions import TypedDict

from app.config import settings
from app.core.scheduler.job_dispatcher import enqueue_schedule_run
from app.core.scheduler.models import ScheduleConfig
from app.core.scheduler.scheduler_lock import LockHeartbeatState as SchedulerHeartbeatState
from app.utils.logger import close_run, get_logger


logger = get_logger(__name__)


class SchedulerFailureResult(TypedDict, total=False):
    """External result payload returned by failed scheduler executions."""

    success: bool
    status: str
    error: str
    message: str
    log_file: str
    lock_lost: bool


def start_lock_heartbeat(
    concurrency,
    schedule_id: str,
    interval: Optional[float] = None,
    clock=None,
) -> SchedulerHeartbeatState:
    """Start the scheduler lock heartbeat and return the live state dict."""
    from app.core.scheduler.scheduler_lock import LockHeartbeat

    if interval is None:
        interval = float(settings.SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS)

    hb = LockHeartbeat(
        concurrency=concurrency,
        schedule_id=schedule_id,
        interval=interval,
        clock=clock,
    )
    hb.start()
    state = hb.get_state_dict()
    state["_hb"] = hb
    return state


def stop_lock_heartbeat(state: SchedulerHeartbeatState) -> None:
    """Stop a heartbeat state created by start_lock_heartbeat.

    Also accepts the legacy state shape used before the LockHeartbeat
    object was stored under ``_hb``.
    """
    if not state:
        return
    hb = state.get("_hb")
    if hb is not None:
        hb.stop()
        return

    stop_event = state.get("stop_event")
    thread = state.get("thread")
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)


def handle_failure(
    *,
    schedule_service,
    log_service,
    policy,
    config: ScheduleConfig,
    schedule_id: str,
    triggered_by: str,
    retry_attempt: int,
    error_message: str,
    steps: list,
    start_time: float,
    run_id: str = "",
    scheduled_for: Optional[str] = None,
) -> SchedulerFailureResult:
    """Finalize a failed scheduler run and decide retry vs terminal failure."""
    total_duration = int((time.time() - start_time) * 1000)

    decision = policy.decide_after_failure(config, retry_attempt, error_message)

    entry = log_service.create_log_entry(
        schedule_id=schedule_id,
        event=decision["event"],
        triggered_by=triggered_by,
        level=decision["log_level"],
        steps=steps,
        total_duration_ms=total_duration,
        error_message=error_message,
        pipeline_info={
            "project_id": config.pipeline.project_id,
            "project_name": config.pipeline.project_name,
            "project_type": config.pipeline.project_type,
        },
        execution_info={
            "use_cache": config.execution.use_cache,
            "retry_attempt": retry_attempt,
        },
    )
    log_path = log_service.write_log(entry)

    if run_id:
        steps_json = [
            {
                "module": s.step.lower(),
                "status": s.status,
                "rows_out": s.rows or 0,
                "duration_ms": s.duration_ms or 0,
            }
            for s in steps
        ]
        try:
            from app.utils.debug_payload_builder import DebugPayloadBuilder

            debug_payload = DebugPayloadBuilder().build(
                run_id=run_id,
                project_id=config.pipeline.project_id,
                steps=steps_json,
                failure={
                    "node_id": None,
                    "node_type": None,
                    "node_label": None,
                    "module": config.pipeline.project_type,
                    "error_type": "SchedulerError",
                    "error_message": error_message,
                    "traceback": None,
                    "occurred_at_ms": total_duration,
                },
            )
        except Exception as e:
            logger.debug(
                f"DebugPayloadBuilder fallito (scheduler): "
                f"{type(e).__name__}: {e}"
            )
            debug_payload = None

        close_run(
            run_id=run_id,
            status="failure",
            duration_ms=total_duration,
            steps=steps_json,
            error_module=config.pipeline.project_type,
            error_message=error_message,
            debug_payload=debug_payload,
        )

    if decision["action"] == "retry":
        logger.warning(
            f"Schedule {schedule_id} fallito (tentativo {retry_attempt}), "
            f"retry tra {decision['wait_seconds']}s"
        )

        schedule_service.set_runtime_status(schedule_id, "running")

        try:
            enqueue_schedule_run(
                schedule_id,
                triggered_by=triggered_by,
                retry_attempt=retry_attempt + 1,
                scheduled_for=scheduled_for,
                countdown=decision["wait_seconds"],
            )
            logger.info(
                f"Retry schedulato: {schedule_id} "
                f"tra {decision['wait_seconds']}s"
            )
        except Exception as e:
            logger.error(f"[CRASH] Errore scheduling retry: {e}", exc_info=True)
            schedule_service.update_runtime(
                schedule_id,
                status="failed",
                last_execution={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "rows_written": 0,
                    "duration_ms": total_duration,
                    "log_file": log_path,
                },
            )

        return {
            "success": False,
            "status": "retry",
            "message": f"Retry {retry_attempt + 1} tra {decision['wait_seconds']}s",
            "log_file": log_path,
        }

    logger.error(
        f"[GESTITO] Schedule {schedule_id} fallito definitivamente "
        f"dopo {retry_attempt} tentativi"
    )

    schedule_service.update_runtime(
        schedule_id,
        status="failed",
        last_execution={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "rows_written": 0,
            "duration_ms": total_duration,
            "log_file": log_path,
        },
    )

    return {
        "success": False,
        "status": "failed",
        "error": "schedule_execution_fatal",
        "message": error_message,
        "log_file": log_path,
    }
