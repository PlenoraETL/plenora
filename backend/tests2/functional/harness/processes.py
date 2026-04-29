"""Process runner minimale per server/worker nei test funzionali."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.request import urlopen


@dataclass
class ManagedProcess:
    """Wrapper leggero attorno a un processo figlio."""

    process: subprocess.Popen
    name: str

    def terminate(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()

    def kill(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.kill()

    def wait(self, timeout: float = 10.0) -> int:
        return self.process.wait(timeout=timeout)


def spawn_process(
    *,
    name: str,
    command: Iterable[str],
    workdir: Path,
    env: Optional[dict] = None,
) -> ManagedProcess:
    """Avvia un processo locale senza shell.

    IMPORTANTE: la stdout viene drenata continuamente da un thread
    background e accumulata in memoria su `proc.captured_output`.
    Senza il drain, un worker Celery chiacchierone (tanti log) riempie
    il buffer del pipe del sistema operativo (4KB su Windows, ~64KB su
    Linux) e i suoi successivi write bloccano — causando falsi hang
    del worker indistinguibili da un deadlock applicativo.
    """
    import threading
    proc = subprocess.Popen(
        list(command),
        cwd=str(workdir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )

    # Drain thread: legge riga per riga e accumula su un buffer in
    # memoria. È un daemon thread: muore con il processo di test.
    captured: list[str] = []
    def _drain():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                captured.append(line)
        except Exception:
            pass

    t = threading.Thread(target=_drain, name=f"drain-{name}", daemon=True)
    t.start()

    managed = ManagedProcess(process=proc, name=name)
    # Attach captured buffer + thread so callers can introspect later
    managed.captured_output = captured  # type: ignore[attr-defined]
    managed.drain_thread = t            # type: ignore[attr-defined]
    return managed


def wait_for_http_ready(
    url: str,
    *,
    timeout_s: float = 20.0,
    process: Optional["ManagedProcess"] = None,
) -> None:
    """Attende che un endpoint HTTP locale risponda 2xx/3xx.

    Args:
        url: endpoint da pollare.
        timeout_s: deadline assoluta (default 20s).
        process: se passato, in caso di timeout il RuntimeError include
            l'exit code del processo (None = ancora vivo, int = morto)
            e le ultime ~80 righe di stdout/stderr catturate da
            spawn_process. Permette di vedere PERCHE' uvicorn (o altro
            child) e' uscito prima di ascoltare, invece di vedere solo
            "Connection refused" senza contesto. Comportamento
            invariato se process=None.
    """
    deadline = time.time() + timeout_s
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.5) as resp:
                if 200 <= getattr(resp, "status", 200) < 400:
                    return
        except Exception as exc:  # pragma: no cover - harness infra
            last_error = exc
            time.sleep(0.25)

    if process is None:
        raise RuntimeError(f"Service not ready at {url}: {last_error}")

    # Diagnostica arricchita: exit code + tail dell'output catturato.
    # Permette al CI log di mostrare il traceback del child che e'
    # uscito prima di ascoltare (es. ImportError in app.main, missing
    # env, OperationalError DB, ecc.).
    poll_status = process.process.poll()
    exit_state = (
        "still running" if poll_status is None
        else f"exited with code {poll_status}"
    )
    captured = getattr(process, "captured_output", None) or []
    tail_lines = captured[-80:] if captured else []
    tail_block = "".join(tail_lines).rstrip()
    if not tail_block:
        tail_block = "(no output captured)"
    raise RuntimeError(
        f"Service not ready at {url}: {last_error}\n"
        f"--- child process [{process.name}] {exit_state} ---\n"
        f"{tail_block}\n"
        f"--- end output ---"
    )


def stop_process(proc: ManagedProcess, *, timeout_s: float = 10.0) -> None:
    """Termina un processo in modo best-effort."""
    try:
        proc.terminate()
        proc.wait(timeout=timeout_s)
    except Exception:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
