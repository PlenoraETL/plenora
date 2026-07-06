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
from app.core.arricchisci.retry_policy import clamp_retry_after
from app.shared.resources.json_path import extract_json_value


_DEFAULT_STATUS_RETRY_CODES = frozenset({429, 500, 502, 503, 504})
_DEFAULT_MAX_RETRY_AFTER_SECONDS = 300


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
        before_poll_request: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._execute = execute
        self._before_poll_request = before_poll_request
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
        retry_status = _status_codes_from_config(
            cfg,
            "retry_status",
            _DEFAULT_STATUS_RETRY_CODES,
        )
        started = self._clock()
        polls = 0

        while True:
            if polls > 0 and self._clock() - started >= max_wait:
                raise TimeoutError("API async job non completato entro max_wait_s")

            polls += 1
            self._run_before_poll_request()
            status_response = self._execute(self._status_spec(
                submit_spec,
                status_url,
                cfg,
            ))
            if status_response.status_code != 200:
                if (
                    status_response.status_code in retry_status
                    and (max_polls is None or polls < max_polls)
                    and self._clock() - started < max_wait
                ):
                    retry_delay = _poll_retry_delay(
                        status_response,
                        poll_interval,
                        cfg,
                    )
                    remaining = max(0.0, max_wait - (self._clock() - started))
                    if retry_delay > 0 and remaining > 0:
                        self._sleep(min(retry_delay, remaining))
                    poll_interval = min(
                        poll_interval * poll_backoff,
                        poll_max_interval,
                    )
                    continue
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
                    retry_status=retry_status,
                    poll_interval=poll_interval,
                    poll_backoff=poll_backoff,
                    poll_max_interval=poll_max_interval,
                    max_wait=max_wait,
                    started=started,
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

            remaining = max(0.0, max_wait - (self._clock() - started))
            if poll_interval > 0 and remaining > 0:
                self._sleep(min(poll_interval, remaining))
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
        retry_status: set[int],
        poll_interval: float,
        poll_backoff: float,
        poll_max_interval: float,
        max_wait: float,
        started: float,
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
            result_spec = RequestSpec(
                url=result_url,
                method=str(cfg.get("result_method", "GET")),
                headers=submit_spec.headers,
                timeout=submit_spec.timeout,
                verify=submit_spec.verify,
                allow_redirects=submit_spec.allow_redirects,
                body_type="none",
                context="API async result",
            )
            result_response = self._execute_idempotent_async_request(
                result_spec,
                cfg,
                retry_status=retry_status,
                poll_interval=poll_interval,
                poll_backoff=poll_backoff,
                poll_max_interval=poll_max_interval,
                max_wait=max_wait,
                started=started,
            )
            if result_response.status_code != 200:
                raise AsyncPollError(
                    "API async result error: "
                    f"HTTP {result_response.status_code} - {result_response.text[:200]}",
                    response=result_response,
                    stage="result",
                )
            return _safe_response_json(result_response), result_response

        raise ValueError(f"async.result_from non supportato: {result_from}")

    def _execute_idempotent_async_request(
        self,
        spec: RequestSpec,
        cfg: Mapping[str, Any],
        *,
        retry_status: set[int],
        poll_interval: float,
        poll_backoff: float,
        poll_max_interval: float,
        max_wait: float,
        started: float,
    ) -> NormalizedResponse:
        attempts = 0
        retry_interval = poll_interval
        while True:
            if attempts > 0 and self._clock() - started >= max_wait:
                raise TimeoutError("API async job non completato entro max_wait_s")

            attempts += 1
            self._run_before_poll_request()
            response = self._execute(spec)
            if response.status_code == 200 or response.status_code not in retry_status:
                return response

            remaining = max(0.0, max_wait - (self._clock() - started))
            if remaining <= 0:
                raise TimeoutError("API async job non completato entro max_wait_s")

            retry_delay = _poll_retry_delay(response, retry_interval, cfg)
            if retry_delay > 0:
                self._sleep(min(retry_delay, remaining))
            retry_interval = min(retry_interval * poll_backoff, poll_max_interval)

    def _run_before_poll_request(self) -> None:
        if self._before_poll_request is not None:
            self._before_poll_request()


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
    if not _is_absolute_or_base_url_template(cfg.get("status_url")):
        raise ValueError("async.status_url deve essere assoluto o contenere {base}")

    result_from = str(cfg.get("result_from", "status_response"))
    if result_from not in {"status_response", "result_url"}:
        raise ValueError(f"async.result_from non supportato: {result_from}")
    if cfg.get("result_url") and not _is_absolute_or_base_url_template(
        cfg.get("result_url")
    ):
        raise ValueError("async.result_url deve essere assoluto o contenere {base}")

    for key in ("accepted_status", "retry_status"):
        status_codes = cfg.get(key)
        if status_codes is not None:
            if not isinstance(status_codes, list):
                raise ValueError(f"async.{key} deve essere una lista")
            try:
                [int(status) for status in status_codes]
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"async.{key} deve contenere codici HTTP numerici"
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
        "max_retry_after_s",
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


def _status_codes_from_config(
    cfg: Mapping[str, Any],
    key: str,
    default: frozenset[int],
) -> set[int]:
    return {int(status) for status in cfg.get(key, default)}


def _is_absolute_or_base_url_template(value: Any) -> bool:
    text = str(value or "").strip()
    if "{base}" in text:
        return True
    return _is_absolute_http_url(text)


def _is_absolute_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _poll_retry_delay(
    response: NormalizedResponse,
    poll_interval: float,
    cfg: Mapping[str, Any],
) -> float:
    if response.status_code == 429:
        raw_max_retry_after = cfg.get(
            "max_retry_after_s",
            _DEFAULT_MAX_RETRY_AFTER_SECONDS,
        )
        max_retry_after = int(float(raw_max_retry_after))
        return float(clamp_retry_after(
            response.headers.get("Retry-After"),
            max_seconds=max_retry_after,
        ))
    return poll_interval


def _format_url_template(value: Any, *, base_url: str, job_id: str) -> str:
    if not value:
        raise ValueError("API async: URL status/result non configurato")
    url = str(value)
    formatted = (
        url.replace("{base}", _origin_from_url(base_url))
        .replace("{job_id}", quote(str(job_id), safe=""))
    )
    if not _is_absolute_http_url(formatted):
        raise ValueError("API async: URL status/result deve essere assoluto")
    return formatted


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
