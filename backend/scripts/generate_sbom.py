"""
Genera SBOM CycloneDX-JSON dell'inventario dipendenze backend.

T-05 Phase 3 Batch D — baseline SBOM ripetibile.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def main() -> int:
    # Path coerenti con la convenzione `backend/scripts/*` -> backend root.
    backend_root = Path(__file__).resolve().parent.parent
    req_main = backend_root / "requirements.txt"
    req_dev = backend_root / "requirements-dev.txt"
    sbom_out = backend_root / "docs" / "compliance" / "sbom.json"

    for req in (req_main, req_dev):
        if not req.exists():
            print(f"ERROR: requirements file mancante: {req}", file=sys.stderr)
            return 2

    sbom_out.parent.mkdir(parents=True, exist_ok=True)

    # pip-audit deve essere importabile: fail-loud se manca.
    if importlib.util.find_spec("pip_audit") is None:
        print(
            "ERROR: pip-audit non installato. Installa con:\n"
            "  pip install -r backend/requirements-dev.txt\n"
            "(pip-audit e' nelle dev-deps dal Phase 3 Batch D / T-05)",
            file=sys.stderr,
        )
        return 3

    # Hardcoded argv: niente shell, niente input utente, idempotente.
    cmd = [
        sys.executable, "-m", "pip_audit",
        "--format", "cyclonedx-json",
        "--output", str(sbom_out),
        "--requirement", str(req_main),
        "--requirement", str(req_dev),
    ]

    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)

    # pip-audit exit code:
    #   0 = nessuna vulnerabilita' trovata
    #   1 = vulnerabilita' trovate (il file SBOM viene comunque scritto)
    # Trattiamo 1 come non-bloccante: lo SBOM e' generato, le CVE vanno
    # triagiate manualmente seguendo `docs/compliance/sbom.md` §3.
    if result.returncode not in (0, 1):
        print(
            f"ERROR: pip-audit exit code {result.returncode}, "
            "SBOM potrebbe non essere stato generato correttamente. "
            "Controlla il file di output prima di committarlo.",
            file=sys.stderr,
        )
        return result.returncode

    if not sbom_out.exists():
        print(
            f"ERROR: pip-audit non ha prodotto il file atteso {sbom_out}.",
            file=sys.stderr,
        )
        return 4

    size_kb = sbom_out.stat().st_size // 1024
    print(f"OK: SBOM generato in {sbom_out} ({size_kb} KB)")

    if result.returncode == 1:
        print(
            "NOTE: pip-audit ha segnalato CVE. Triale seguendo "
            "`docs/compliance/sbom.md` §3 prima di chiudere il refresh.",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
