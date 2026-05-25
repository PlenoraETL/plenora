"""
Contract test per `app.core.manipola.source_loading.normalize_node_columns`.

Cluster bug coperto (commit fd2a5ce6): refactoring guard sulla
centralizzazione dell'helper. Pre-fix lo stesso for-loop era
duplicato in 2 callsite (`source_loading.py::load_file_sources` e
`datasource_node_loading.py::_load_datasource_node`); post-fix
entrambi delegano allo helper modulo.
"""

from __future__ import annotations

import inspect

import pytest

from app.core.manipola.source_loading import normalize_node_columns


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class TestNormalizeNodeColumnsInputs:
    """Contratto input: None/[]/dict/object/mix."""

    def test_none_returns_empty_list(self):
        assert normalize_node_columns(None) == []

    def test_empty_list_returns_empty_list(self):
        assert normalize_node_columns([]) == []

    def test_dict_passes_invariato(self):
        d = {"name": "foo", "alias": "f", "type": "string", "visible": True}
        out = normalize_node_columns([d])
        assert out == [d]
        # Identity: il dict originale e' lo stesso oggetto (no copy).
        assert out[0] is d

    def test_object_converted_to_dict_with_canonical_keys(self):
        class Col:
            def __init__(self, name, alias=None, type_="", visible=True):
                self.name = name
                self.alias = alias
                self.type = type_
                self.visible = visible

        out = normalize_node_columns([
            Col("bar", alias="b", type_="int", visible=False)
        ])
        assert out == [{
            "name": "bar",
            "alias": "b",
            "type": "int",
            "visible": False,
        }]

    def test_mix_dict_and_object(self):
        class Col:
            name = "obj_col"
            alias = None
            type = "str"
            visible = True

        out = normalize_node_columns([
            {"name": "dict_col", "alias": "d"},
            Col(),
        ])
        assert out[0] == {"name": "dict_col", "alias": "d"}
        assert out[1] == {
            "name": "obj_col",
            "alias": None,
            "type": "str",
            "visible": True,
        }

    def test_object_missing_attributes_defaults(self):
        """Object senza alcuni attrs: `getattr` fallback ai default
        '' / None / True."""

        class Bare:
            name = "only_name"

        out = normalize_node_columns([Bare()])
        assert out == [{
            "name": "only_name",
            "alias": None,
            "type": "",
            "visible": True,
        }]


class TestCentralizationArchitectural:
    """I 2 callsite originali NON devono avere il for-loop duplicato."""

    def test_source_loading_no_inline_normalize_loop(self):
        from app.core.manipola import source_loading as sl
        src = inspect.getsource(sl)
        # Conta literal occurrence del loop duplicato originale.
        # Il guard accetta `normalize_node_columns(` chiamato; vieta
        # `node_columns_normalized = []` come variable init (pattern
        # univoco del loop pre-fix).
        count = src.count("node_columns_normalized = []")
        assert count == 0, (
            f"regressione: il loop inline normalize_node_columns "
            f"e' tornato in source_loading.py ({count} occorrenze). "
            f"Delegare a normalize_node_columns()."
        )

    def test_datasource_node_loading_delegates(self):
        from app.core.manipola import datasource_node_loading as dnl
        src = inspect.getsource(dnl)
        assert "normalize_node_columns(" in src, (
            "regressione: datasource_node_loading.py non delega piu' "
            "a normalize_node_columns — duplicazione reintrodotta?"
        )
        count = src.count("node_columns_normalized = []")
        assert count == 0, (
            f"regressione: loop inline reintrodotto in "
            f"datasource_node_loading.py ({count} occorrenze)"
        )

    def test_normalize_in_public_api(self):
        """`normalize_node_columns` deve essere in `__all__` per
        marcarlo come API pubblica del modulo (consumer esterni
        sappiano di poterlo riusare)."""
        from app.core.manipola import source_loading as sl
        assert "normalize_node_columns" in sl.__all__
