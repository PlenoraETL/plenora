"""Gruppi E/O delle condizioni di filtro: UNA regola per Estrai e Manipola.

REGOLA (06/11/2026, formato compatibile)
----------------------------------------
Ogni condizione (filtro WHERE di una tabella Estrai, voce di ``conditions``
del Filtra di Manipola) può portare la chiave facoltativa ``group``: intero
``>= 0``; assente o ``null`` vale ``0``. Condizioni con lo stesso ``group``
sono in E (AND), gruppi diversi in O (OR): ``(g0) OR (g1) OR ...``. L'ordine
dei gruppi è quello di prima apparizione; dentro un gruppo resta l'ordine
della lista. Senza ``group`` (o con tutte le condizioni nello stesso gruppo)
c'è un solo gruppo con la lista intera nell'ordine originale: chi applica i
gruppi produce esattamente il risultato di prima (SQL, parametri, righe).

La lista resta piatta: gli indici delle voci (``filters.N.value``,
``config.conditions.N.value`` dei binding runtime) non cambiano.

:func:`condition_groups` è l'unico punto che legge ``group``: Estrai
(``estrai.query_filters``) e Manipola (``manipola.transforms.filtering``) la
usano entrambi, quindi stessa validazione e stesso raggruppamento.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

GROUP_KEY = "group"


def condition_group(item: Any) -> int:
    """Gruppo di una condizione (dict o oggetto con attributo ``group``).

    ``bool`` è rifiutato anche se in Python è un ``int``: ``true`` nel JSON
    non è un numero di gruppo.
    """
    value = item.get(GROUP_KEY) if isinstance(item, Mapping) else getattr(item, GROUP_KEY, None)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"Filtro: 'group' deve essere un numero intero maggiore o uguale a 0 "
            f"(ricevuto {value!r})."
        )
    return value


def condition_groups(items: Sequence[T] | None) -> list[list[T]]:
    """Condizioni divise per gruppo, gruppi in ordine di prima apparizione.

    ``[]`` o ``None`` -> ``[]``. Ogni voce è validata (:func:`condition_group`)
    prima di raggruppare, così un ``group`` non valido è un errore anche se la
    condizione verrebbe poi saltata.
    """
    groups: dict[int, list[T]] = {}
    for item in items or []:
        groups.setdefault(condition_group(item), []).append(item)
    return list(groups.values())


__all__ = ["GROUP_KEY", "condition_group", "condition_groups"]
