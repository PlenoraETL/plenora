"""Early environment loading boundary for application configuration.

This module owns the operational decision of whether `backend/.env` may be
read from disk. Runtime entry points call it before importing settings so the
Settings object can stay focused on values and type conversion.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path


DOTENV_DISABLED_ENVS: tuple[str, ...] = ("production", "prod", "staging", "stage")
_ENV_LOAD_ATTEMPTED = False


def should_load_dotenv() -> bool:
    """Return True when the current environment may read `backend/.env`."""
    env_name = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    return env_name not in DOTENV_DISABLED_ENVS


def load_backend_dotenv(logger: logging.Logger | None = None) -> None:
    """Load `backend/.env` early for non-prod/staging environments.

    Production/staging explicitly ignore the file with the same warning as the
    previous inline config implementation. Missing `python-dotenv` is tolerated:
    the process keeps using only system environment variables.
    """
    global _ENV_LOAD_ATTEMPTED

    if _ENV_LOAD_ATTEMPTED:
        return
    _ENV_LOAD_ATTEMPTED = True

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    if should_load_dotenv():
        load_dotenv(env_path)
        return

    if logger is not None:
        logger.warning(
            "backend/.env presente ma ENVIRONMENT indica prod/staging: "
            "file ignorato. I secret devono arrivare da env var di sistema."
        )
