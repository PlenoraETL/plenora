"""``python -m app.core.native_pipeline`` entry point."""

from __future__ import annotations

import sys

from app.core.native_pipeline.cli import main


if __name__ == "__main__":
    sys.exit(main())
