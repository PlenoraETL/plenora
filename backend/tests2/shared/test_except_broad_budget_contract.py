"""
Contract test "budget" per `except Exception` broad-catch.

L'audit cross-modulo ha contato 208 occorrenze di `except Exception`
in 50 file dell'app. Un narrow case-by-case e' impraticabile (e in
maggioranza non-desiderabile: la maggior parte sono safety-net
intenzionali su boundary I/O — Redis, FTP, file system, HTTP — e
narrow significherebbe enumerare tutte le possibili exception per
ogni call site, generando codice fragile).

Approccio "budget": lock-in del count attuale. Ogni nuovo `except
Exception` deve essere accompagnato dalla riduzione del budget
(narrowing di un altro broad-catch in altro file), oppure aumento
esplicito del budget con commit message che giustifica il broad
intent. Questo evita la regressione silente "qualcuno aggiunge un
catch broad senza motivo" e produce pressione gradual a narrowing.

Pattern d'uso quando si aggiunge un nuovo `except Exception`:
  1. Verifica che il broad sia INTENZIONALE (safety-net documentato,
     I/O boundary che fallback, top-level handler).
  2. Aggiungi commento adiacente che giustifica il broad.
  3. Bumpa `_EXPECT_BROAD_BUDGET` qui di +N (= numero net di nuovi
     broad-catch nel commit).

Quando si fa narrow di un broad esistente:
  1. Restringi a eccezioni specifiche.
  2. Decrementa `_EXPECT_BROAD_BUDGET` di -N.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Lock attuale: 724 occorrenze totali in `app/` (esclusi tests/,
# scripts/, migrations/). Diminuirebbe ad ogni narrow incrementale;
# aumentarne il valore richiede commit con motivazione esplicita.
# 736 -> 724: cutover IMDF a plenora.indoor.bundle (rimossi loader.py,
# persist_imdf_bundle, open_gdb_in_vsimem e i branch legacy GDB).
_EXPECT_BROAD_BUDGET = 724

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"

# Pattern: `except Exception` con o senza alias (`as e`) e con
# eventuali parentesi multiple (`except (Exception, X)`). Quest'ultimo
# caso e' GIA` narrow (lista esplicita), quindi NON conta come broad.
_BROAD_EXCEPT_RE = re.compile(
    r"^\s*except\s+Exception\s*(?:as\s+\w+)?\s*:",
    re.MULTILINE,
)


def _count_broad_except_in_app() -> tuple[int, dict[str, int]]:
    """Ritorna (total, per_file) dei broad-catch `except Exception:`."""
    per_file: dict[str, int] = {}
    total = 0
    for py_file in _APP_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8", errors="ignore")
        matches = _BROAD_EXCEPT_RE.findall(source)
        if matches:
            rel = py_file.relative_to(_BACKEND_ROOT).as_posix()
            per_file[rel] = len(matches)
            total += len(matches)
    return total, per_file


def test_except_broad_budget_not_exceeded():
    """Il count totale non deve superare il budget hardcoded."""
    total, per_file = _count_broad_except_in_app()

    if total > _EXPECT_BROAD_BUDGET:
        top_offenders = sorted(per_file.items(), key=lambda x: -x[1])[:10]
        lines = [f"  {count:>3}  {rel}" for rel, count in top_offenders]
        raise AssertionError(
            f"`except Exception:` count = {total}, budget = {_EXPECT_BROAD_BUDGET}\n"
            f"Hai aggiunto {total - _EXPECT_BROAD_BUDGET} nuovi broad-catch senza "
            f"riduzione compensativa.\n\n"
            f"Top 10 offender:\n" + "\n".join(lines) + "\n\n"
            f"Se il nuovo broad e' INTENZIONALE (safety-net, I/O fallback):\n"
            f"  1. aggiungi commento adiacente che giustifica\n"
            f"  2. bumpa `_EXPECT_BROAD_BUDGET` a {total} in questo file\n"
            f"Altrimenti narrow l'eccezione a tipi specifici."
        )


def test_except_broad_budget_not_stale():
    """Il budget non e' sotto-stimato: se sei sotto budget, abbassalo.

    Soft check: tolleranza di -5 per evitare flake su narrow paralleli.
    Lo scopo e' costringere a abbassare il budget quando si narrow.
    """
    total, _ = _count_broad_except_in_app()
    drift = _EXPECT_BROAD_BUDGET - total
    if drift > 5:
        raise AssertionError(
            f"`except Exception:` count = {total}, budget = {_EXPECT_BROAD_BUDGET}\n"
            f"Sei sotto-budget di {drift} (hai narrow ma non hai aggiornato il\n"
            f"contract). Abbassa `_EXPECT_BROAD_BUDGET` a {total} in questo file\n"
            f"per fissare il nuovo limite."
        )
