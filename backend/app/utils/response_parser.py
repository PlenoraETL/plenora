"""KEEP_COMPAT wrapper for Arricchisci response parsing.

Canonical target: app.core.arricchisci.response_parser.

Removal condition: delete this module after all external imports switch to
``app.core.arricchisci.response_parser``.
"""

from app.core.arricchisci.response_parser import (  # noqa: F401
    PRESET_CONFIGS,
    ResponseParser,
    get_preset_config,
    parse_response,
)

__all__ = [
    "PRESET_CONFIGS",
    "ResponseParser",
    "get_preset_config",
    "parse_response",
]
