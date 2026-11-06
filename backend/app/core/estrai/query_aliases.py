"""Alias delle tabelle di Estrai: UNA regola per FROM, JOIN, ON, WHERE e SELECT.

REGOLA (06/11/2026, self-join: la stessa tabella più volte nel canvas)
---------------------------------------------------------------------
- L'alias effettivo di un nodo tabella è :func:`effective_alias`: l'alias
  scritto dall'utente, altrimenti il nome della tabella. È l'unico punto che
  lo calcola (``test_estrai_alias_contract`` vieta ``node.alias or`` altrove
  in ``app/core/estrai``).
- Prima della generazione :func:`with_unique_aliases` rende gli alias univoci
  per costruzione, nell'ordine delle tabelle della query (la prima è il
  FROM): il primo nodo con un alias effettivo lo tiene; un nodo successivo con
  un alias effettivo già preso riceve l'alias automatico ``{tabella}_{n}``
  (n = 2, 3, ... saltando quelli già presi o dichiarati da altri nodi). Il
  confronto ignora maiuscole/minuscole, come gli identificatori SQL non
  quotati. Il passo lavora su COPIE dei nodi: il canvas salvato non cambia.
- Senza alias effettivi ripetuti i nodi restituiti sono gli stessi oggetti di
  prima: SQL, parametri e colonne identici.

FROM/JOIN emettono ``tabella alias`` quando il nodo ha un alias (anche
automatico); ON, WHERE e SELECT usano sempre :func:`effective_alias`. Le
colonne omonime dei due nodi seguono la regola di sempre delle colonne in
uscita (``shared.execution.output_names``: ``ID``, ``ID_2``, ...).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, List


def effective_alias(node: Any) -> str:
    """Alias con cui il SQL riferisce la tabella del nodo."""
    return getattr(node, "alias", None) or getattr(node, "table_name", None) or ""


_COMBINING_MARKS = re.compile("[̀-ͯ]")


def alias_base(table_name: str | None) -> str:
    """Base dell'alias automatico: UNA regola con il frontend.

    ``frontend/src/utils/estraiJoins.selfJoinAliasBase``, casi condivisi in
    ``frontend/tests/fixtures/selfJoinAliases.json`` (conferma UI, B-K11):
    ultima parte del nome (senza schema); lettere accentate senza accento
    (NFD, segni U+0300–U+036F tolti); ogni altro carattere fuori da
    ``A-Z a-z 0-9 _`` diventa ``_`` (prima ``\\W`` Unicode: «città» restava
    «città» qui e diventava «citt_» nel frontend); vuoto = ``t``; ``t_``
    davanti a ciò che non comincia con una lettera ASCII o ``_``.
    """
    table = (table_name or "").rsplit(".", 1)[-1]
    plain = _COMBINING_MARKS.sub("", unicodedata.normalize("NFD", table))
    base = re.sub(r"[^A-Za-z0-9_]", "_", plain) or "t"
    return base if re.match(r"[A-Za-z_]", base) else f"t_{base}"


def _alias_base(node: Any) -> str:
    return alias_base(getattr(node, "table_name", None))


def with_unique_aliases(table_nodes: List[Any]) -> List[Any]:
    """Nodi della query con alias effettivi univoci (copie solo dove serve)."""
    declared = {effective_alias(node).lower() for node in table_nodes}
    taken: set[str] = set()
    out = []
    for node in table_nodes:
        alias = effective_alias(node)
        if alias.lower() not in taken:
            taken.add(alias.lower())
            out.append(node)
            continue
        base, suffix = _alias_base(node), 2
        while f"{base}_{suffix}".lower() in taken | declared:
            suffix += 1
        alias = f"{base}_{suffix}"
        taken.add(alias.lower())
        out.append(node.model_copy(update={"alias": alias}))
    return out


__all__ = ["alias_base", "effective_alias", "with_unique_aliases"]
