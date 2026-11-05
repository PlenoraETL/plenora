"""Genera ``runtime_input_kinds.json``: la tabella dei tipi di input runtime.

La fonte e' ``app.shared.execution.runtime_input_kinds`` (backend). Il test
``test_runtime_input_kinds_parity.py`` verifica che il file sia aggiornato e
che i modelli (manifest, canvas) usino la stessa tabella; il frontend
(``frontend/tests/shared/runtimeInputKinds.contract.test.js``) confronta la
sua copia (``frontend/src/utils/runtimeInputKinds.js``) con lo stesso file.

Uso (da backend/):  python -m tests2.contracts.generate_runtime_input_kinds
"""

from __future__ import annotations

import json
from pathlib import Path

from app.shared.execution.runtime_input_kinds import contract_rows

KINDS_PATH = Path(__file__).with_name("runtime_input_kinds.json")


def build_rows() -> list[dict[str, object]]:
    return contract_rows()


def main() -> None:
    KINDS_PATH.write_text(json.dumps(build_rows(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
