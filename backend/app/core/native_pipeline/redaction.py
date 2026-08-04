"""Secret redaction for everything the native path exposes.

Redaction runs on raw child output *before* it is parsed, so the parsed report,
the stage diagnostics, the error messages and the CLI document all inherit the
same masking. The replacement token contains no JSON metacharacters, which is
what keeps redact-then-parse safe.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence


REDACTED = "***REDACTED***"

# A URL carrying credentials is the shape a DSN takes in tool output. The
# character class stops at quotes and whitespace so a DSN embedded in a JSON
# string is masked without swallowing the closing quote.
_URL_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*)(?:://|:\\/\\/)[^\s\"'<>]+"
)

# Key/value secrets that appear in connection strings and tool logs.
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*=\s*[^\s;,\"']+"
)


@dataclass(frozen=True, slots=True)
class SecretRedactor:
    """Masks known secret values plus secret-shaped text."""

    secrets: tuple[str, ...] = ()

    def redact(self, text: str) -> str:
        """Return ``text`` with known and secret-shaped values masked."""
        masked = _URL_PATTERN.sub(rf"\1://{REDACTED}", text)
        masked = _ASSIGNMENT_PATTERN.sub(rf"\1={REDACTED}", masked)
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret and secret.strip():
                masked = masked.replace(secret, REDACTED)
        return masked

    def redact_argv(self, argv: Sequence[str]) -> tuple[str, ...]:
        """Redact an argv list for reporting.

        Stage argv never carries a secret by construction (the DSN travels as
        an environment variable *name*); this is defence in depth for paths or
        flags a future stage might add.
        """
        return tuple(self.redact(str(part)) for part in argv)


__all__ = ["REDACTED", "SecretRedactor"]
