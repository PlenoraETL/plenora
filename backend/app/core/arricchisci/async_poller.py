"""Async submit/poll/result overlay for Arricchisci REST connectors."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

from app.core.arricchisci.http_executor import (
    NormalizedResponse,
    RequestSpec,
    execute_http_request,
)
from app.shared.resources.json_path import extract_json_value


@dataclass(frozen=True)
class AsyncPollResult:
    completed: bool
    data: Any
    response: NormalizedResponse
    job_id: str
    status: Any
    status_data: Any
    submit_response: NormalizedResponse


class AsyncPollError(ValueError):
    """Async polling error with the HTTP response that caused it, when any."""

    def __init__(
        self,
        message: str,
        *,
        response: NormalizedResponse | None = None,
        stage: str = "",
    ) -> None:
        super().__init__(message)
        self.response = response
        self.stage = stage


class AsyncPoller:
    """Run a REST async flow: submit once, poll status, return final data."""

    def __init__(
        self,
        *,
        execute: Callable[[RequestSpec], NormalizedResponse] = execute_http_request,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._execute = execute
        self._sleep = sleep
        self._clock = clock

    def run(
        self,
        submit_spec: RequestSpec,
        async_config: Mapping[str, Any],
        *,
        base_url: str,
        max_polls: int | None = None,
        allow_incomplete: bool = False,
    ) -> AsyncPollResult:
        validate_async_config(async_config)
        cfg = dict(async_config or {})
        submit_response = self._execute(submit_spec)
        accepted_status = {
            int(status) for status in cfg.get("accepted_status", [200, 202])
        }
        if submit_response.status_code not in accepted_status:
            raise AsyncPollError(
                "API async submit error: "
                f"HTTP {submit_response.status_code} - {submit_response.text[:200]}",
                response=submit_response,
                stage="submit",
            )

        job_id_source = str(cfg.get("job_id_from", "body"))
        submit_data = (
            _safe_response_json(submit_response)
            if job_id_source == "body"
            else None
        )
        job_id = _extract_job_id(submit_response, submit_data, cfg)
        status_url = _format_url_template(
            cfg.get("status_url"),
            base_url=base_url,
            job_id=job_id,
        )

        poll_interval = float(cfg.get("poll_interval_s", 3))
        poll_backoff = float(cfg.get("poll_backoff", 1.0))
        poll_max_interval = float(cfg.get("poll_max_interval_s", 30))
        max_wait = float(cfg.get("max_wait_s", 600))
        started = self._clock()
        polls = 0

        while True:
            polls += 1
            status_response = self._execute(self._status_spec(
                submit_spec,
                status_url,
                cfg,
            ))
            if status_response.status_code != 200:
                raise AsyncPollError(
                    "API async status error: "
                    f"HTTP {status_response.status_code} - {status_response.text[:200]}",
                    response=status_response,
                    stage="status",
                )

            status_data = _safe_response_json(status_response)
            _raise_for_async_error(status_data, cfg)

            status = extract_json_value(
                status_data,
                str(cfg.get("status_path", "status")),
            )
            if _value_in(status, cfg.get("failed_values", ["failed", "cancelled"])):
                raise ValueError(f"API async job failed: {status}")

            if _value_in(status, cfg.get("done_values", ["completed"])):
                data, response = self._resolve_result(
                    submit_spec,
                    status_data,
                    status_response,
                    cfg,
                    base_url=base_url,
                    job_id=job_id,
                )
                return AsyncPollResult(
                    completed=True,
                    data=data,
                    response=response,
                    job_id=str(job_id),
                    status=status,
                    status_data=status_data,
                    submit_response=submit_response,
                )

            if max_polls is not None and polls >= max_polls:
                if allow_incomplete:
                    return AsyncPollResult(
                        completed=False,
                        data=status_data,
                        response=status_response,
                        job_id=str(job_id),
                        status=status,
                        status_data=status_data,
                        submit_response=submit_response,
                    )
                raise TimeoutError("API async job non completato entro i poll consentiti")

            if self._clock() - started >= max_wait:
                raise TimeoutError("API async job non completato entro max_wait_s")

            if poll_interval > 0:
                self._sleep(poll_interval)
            poll_interval = min(poll_interval * poll_backoff, poll_max_interval)

    @staticmethod
    def _status_spec(
        submit_spec: RequestSpec,
        status_url: str,
        cfg: Mapping[str, Any],
    ) -> RequestSpec:
        return RequestSpec(
            url=status_url,
            method=str(cfg.get("status_method", "GET")),
            headers=submit_spec.headers,
            timeout=submit_spec.timeout,
            verify=submit_spec.verify,
            allow_redirects=submit_spec.allow_redirects,
            body_type="none",
            context="API async status",
        )

    def _resolve_result(
        self,
        submit_spec: RequestSpec,
        status_data: Any,
        status_response: NormalizedResponse,
        cfg: Mapping[str, Any],
        *,
        base_url: str,
        job_id: str,
    ) -> tuple[Any, NormalizedResponse]:
        result_from = str(cfg.get("result_from", "status_response"))
        result_path = cfg.get("result_path", "result")

        if result_from == "status_response":
            data = (
                extract_json_value(status_data, str(result_path))
                if result_path
                else status_data
            )
            if data is None:
                data = status_data
            return data, status_response

        if result_from == "result_url":
            result_url = cfg.get("result_url")
            if not result_url and result_path:
                result_url = extract_json_value(status_data, str(result_path))
            result_url = _format_url_template(
                result_url,
                base_url=base_url,
                job_id=job_id,
            )
            result_response = self._execute(RequestSpec(
                url=result_url,
                method=str(cfg.get("result_method", "GET")),
                headers=submit_spec.headers,
                timeout=submit_spec.timeout,
                verify=submit_spec.verify,
                allow_redirects=submit_spec.allow_redirects,
                body_type="none",
                context="API async result",
            ))
            if result_response.status_code != 200:
                raise AsyncPollError(
                    "API async result error: "
                    f"HTTP {result_response.status_code} - {result_response.text[:200]}",
                    response=result_response,
                    stage="result",
                )
            return _safe_response_json(result_response), result_response

        raise ValueError(f"async.result_from non supportato: {result_from}")


def validate_async_config(async_config: Mapping[str, Any]) -> None:
    """Validate static async config before a submit request is sent."""
    if not async_config:
        return
    if not isinstance(async_config, Mapping):
        raise ValueError("async deve essere un oggetto di configurazione")

    cfg = dict(async_config or {})
    if not cfg.get("enabled"):
        return

    job_id_from = str(cfg.get("job_id_from", "body"))
    if job_id_from not in {"body", "header"}:
        raise ValueError(f"async.job_id_from non supportato: {job_id_from}")

    if not cfg.get("job_id_path", "id"):
        raise ValueError("async.job_id_path non configurato")

    if not cfg.get("status_url"):
        raise ValueError("async.status_url non configurato")

    result_from = str(cfg.get("result_from", "status_response"))
    if result_from not in {"status_response", "result_url"}:
        raise ValueError(f"async.result_from non supportato: {result_from}")

    accepted_status = cfg.get("accepted_status")
    if accepted_status is not None:
        if not isinstance(accepted_status, list):
            raise ValueError("async.accepted_status deve essere una lista")
        try:
            [int(status) for status in accepted_status]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "async.accepted_status deve contenere codici HTTP numerici"
            ) from exc

    for key in ("done_values", "failed_values"):
        value = cfg.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"async.{key} deve essere una lista")

    for key in (
        "poll_interval_s",
        "poll_backoff",
        "poll_max_interval_s",
        "max_wait_s",
    ):
        if key in cfg:
            try:
                float(cfg[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"async.{key} deve essere numerico") from exc


def _extract_job_id(
    response: NormalizedResponse,
    data: Any,
    cfg: Mapping[str, Any],
) -> str:
    source = str(cfg.get("job_id_from", "body"))
    path = str(cfg.get("job_id_path", "id"))
    if source == "header":
        job_id = response.headers.get(path)
    elif source == "body":
        job_id = extract_json_value(data, path)
    else:
        raise ValueError(f"async.job_id_from non supportato: {source}")
    if job_id in (None, ""):
        raise ValueError("API async submit: job id non trovato")
    return str(job_id)


def _format_url_template(value: Any, *, base_url: str, job_id: str) -> str:
    if not value:
        raise ValueError("API async: URL status/result non configurato")
    url = str(value)
    return (
        url.replace("{base}", _origin_from_url(base_url))
        .replace("{job_id}", quote(str(job_id), safe=""))
    )


def _origin_from_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def _safe_response_json(response: NormalizedResponse) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(
            f"API async response non JSON valido: {type(exc).__name__}"
        ) from exc


def _raise_for_async_error(data: Any, cfg: Mapping[str, Any]) -> None:
    error_path = cfg.get("error_path")
    if not error_path:
        return
    error_value = extract_json_value(data, str(error_path))
    if error_value:
        raise ValueError(f"API async error: {_format_error_value(error_value)}")


def _format_error_value(error_value: Any) -> str:
    if isinstance(error_value, Mapping):
        for key in ("detail", "error", "message"):
            value = error_value.get(key)
            if value:
                return str(value)
    return str(error_value)


def _value_in(value: Any, candidates: Any) -> bool:
    if not isinstance(candidates, list):
        return False
    if value in candidates:
        return True
    normalized = str(value).strip().lower()
    return any(normalized == str(candidate).strip().lower() for candidate in candidates)
