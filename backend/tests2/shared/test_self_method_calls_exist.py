"""Le chiamate `self.metodo(...)` degli esecutori corrispondono a metodi esistenti.

Collaudo dal vivo del Blocco A (04/11/2026): la A1 aveva tolto
`ManipolaExecutor._costruisci_mappa_tipi_canvas`, ancora chiamato dal
salvataggio dell'output: ogni esecuzione Manipola falliva e nessun test
copriva quel percorso. Per costruzione, per ogni esecutore (classi concrete)
ogni chiamata a un metodo di `self` deve risolversi su un attributo.
"""
import ast
import inspect

import pytest

from app.core.arricchisci.executor import EnrichExecutor
from app.core.estrai.executor import EstraiExecutor
from app.core.manipola.executor import ManipolaExecutor

pytestmark = [pytest.mark.unit, pytest.mark.contract]

EXECUTORS = [ManipolaExecutor, EstraiExecutor, EnrichExecutor]


def _self_calls_and_assigns(cls):
    tree = ast.parse(inspect.getsource(inspect.getmodule(cls)))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls.__name__)
    calls, assigned = set(), set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self"):
            calls.add(sub.func.attr)
        targets = sub.targets if isinstance(sub, ast.Assign) else (
            [sub.target] if isinstance(sub, (ast.AnnAssign, ast.AugAssign)) else [])
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                assigned.add(t.attr)
    return calls, assigned


@pytest.mark.parametrize("cls", EXECUTORS, ids=lambda c: c.__name__)
def test_ogni_chiamata_self_metodo_esiste(cls):
    calls, assigned = _self_calls_and_assigns(cls)
    missing = sorted(name for name in calls - assigned if not hasattr(cls, name))
    assert missing == []
