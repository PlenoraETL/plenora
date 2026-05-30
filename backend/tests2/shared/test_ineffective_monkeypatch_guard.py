"""Guard architetturale: nessun monkeypatch ineffettivo su import by-name.

Failure mode coperto (falsa sicurezza):
  La produzione fa `from M import f` a LIVELLO MODULO -> il consumer tiene una
  copia cached di `f` nel proprio namespace. Un test che patcha `M.f`
  (`monkeypatch.setattr(M, "f", ...)`, `patch.object(M, "f")`, `patch("M.f")`)
  NON intercetta quella copia: il consumer continua a chiamare la `f` reale.
  Il test passa/fallisce per il motivo sbagliato, o non controlla affatto la
  variabile che crede di controllare.

  Gli import `from M import f` *dentro una funzione* (lazy) NON sono a rischio:
  vengono rivalutati a ogni chiamata e quindi VEDONO la patch. Per questo lo
  scanner considera SOLO gli import top-level (cached all'import del modulo).

Fix quando il guard fallisce: patchare il binding del *call-site*, cioe'
`patch("<consumer_module>.f", ...)` invece di `patch("M.f", ...)`.

Lo scanner e' una euristica conservativa (condizione necessaria, non
sufficiente): puo' segnalare un falso positivo se il test importa un consumer
top-level solo incidentalmente mentre il path che esercita risolve `f` via un
import lazy. Tali casi vanno in `_ALLOWLIST` con motivazione.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_DIRS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers")
_TEST_DIR = _BACKEND_ROOT / "tests2"


# Falsi positivi noti, chiave (test_file, home_module, symbol) — niente numeri
# di riga (stabile ai refactor). Ogni voce richiede una motivazione.
_ALLOWLIST: set[tuple[str, str, str]] = {
    # Il test esercita ApiConnectionSupportMixin._write_intermediate_snapshot,
    # che importa record_checkpoint_snapshot in modo LAZY (effettivo); aggancia
    # manipola.executor (import top-level) solo perche' lo importa per helper.
    (
        "tests2/intermediate/unit/test_b2b_propagation_manipola_arricchisci_contract.py",
        "app.core.intermediate.snapshot",
        "record_checkpoint_snapshot",
    ),
}


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(_BACKEND_ROOT).with_suffix("").parts)


def _iter_py(d: Path):
    yield from sorted(d.rglob("*.py"))


def _scan_production() -> tuple[dict[tuple[str, str], set[str]], dict[str, set[str]]]:
    """(home_module, symbol) -> {consumer_modules}  +  module -> {top-level defs}.

    Solo `from M import f` a livello modulo (cached) entra in `byname`.
    """
    byname: dict[tuple[str, str], set[str]] = defaultdict(set)
    module_defs: dict[str, set[str]] = defaultdict(set)
    for d in _APP_DIRS:
        if not d.exists():
            continue
        for p in _iter_py(d):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            mod = _dotted(p)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    module_defs[mod].add(node.name)
                if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    for alias in node.names:
                        if alias.name != "*":
                            byname[(node.module, alias.name)].add(mod)
    return byname, module_defs


def _test_imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods


def _alias_to_module(tree: ast.AST) -> dict[str, str]:
    amap: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                amap[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                amap[a.asname or a.name] = f"{node.module}.{a.name}"
    return amap


def _patch_targets(call: ast.Call, amap: dict[str, str]):
    """Yield (home_module, symbol, kind) for a patch-like call."""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr in ("setattr", "object"):
        if (
            len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)
            and isinstance(call.args[0], ast.Name)
        ):
            home = amap.get(call.args[0].id)
            if home:
                kind = "monkeypatch.setattr" if f.attr == "setattr" else "patch.object"
                yield home, call.args[1].value, kind
    elif (isinstance(f, ast.Name) and f.id == "patch") or (
        isinstance(f, ast.Attribute) and f.attr == "patch"
    ):
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(
            call.args[0].value, str
        ) and "." in call.args[0].value:
            home, sym = call.args[0].value.rsplit(".", 1)
            yield home, sym, 'patch("...")'


def _scan() -> list[tuple[str, int, str, str, str, list[str]]]:
    byname, module_defs = _scan_production()
    findings: list[tuple[str, int, str, str, str, list[str]]] = []
    for p in _iter_py(_TEST_DIR):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        amap = _alias_to_module(tree)
        test_mods = _test_imported_modules(tree)
        tf = p.relative_to(_BACKEND_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for home, sym, kind in _patch_targets(node, amap):
                consumers = byname.get((home, sym))
                if not (consumers and sym in module_defs.get(home, set())):
                    continue
                sut = sorted(c for c in consumers if c in test_mods)
                if sut:
                    findings.append((tf, node.lineno, home, sym, kind, sut))
    return findings


def test_no_ineffective_home_module_monkeypatch() -> None:
    findings = _scan()
    offenders = [
        f for f in findings if (f[0], f[2], f[3]) not in _ALLOWLIST
    ]
    if offenders:
        lines = [
            f"  {tf}:{ln}  patch({home}.{sym}) [{kind}]\n"
            f"      consumer top-level by-name (cached): {sut}\n"
            f"      -> patcha il call-site: patch(\"{sut[0]}.{sym}\", ...)"
            for tf, ln, home, sym, kind, sut in offenders
        ]
        pytest.fail(
            "Monkeypatch ineffettivi: il target patcha il modulo-home ma il "
            "consumer tiene una copia cached (import by-name top-level), quindi "
            "gira la callable reale.\n" + "\n".join(lines)
        )

    # Le voci di allowlist devono restare valide: se un falso positivo non e'
    # piu' rilevato (refactor), va rimosso per non mascherare regressioni future.
    detected = {(f[0], f[2], f[3]) for f in findings}
    stale = _ALLOWLIST - detected
    assert not stale, (
        "Voci di _ALLOWLIST non piu' rilevate (rimuovile):\n  "
        + "\n  ".join(f"{tf} :: {home}.{sym}" for tf, home, sym in sorted(stale))
    )
