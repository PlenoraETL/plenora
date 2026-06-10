"""
Test comportamentali per QueryJoinRenderer (app/core/estrai/query_joins.py).

Il renderer costruisce le clausole FROM/JOIN riutilizzando gli helper di
validazione di QueryGenerator. Per esercitare il comportamento *reale* (e non
un doppio fragile) costruiamo un vero QueryGenerator e gli passiamo Node/Edge
Pydantic autentici: cosi' le bind dei metodi (`_qualify_table_name`,
`_is_valid_identifier`, `_genera_join`, ...) sono quelle di produzione.
"""

from __future__ import annotations

import pytest

from app.core.estrai.query_generator import QueryGenerator
from app.core.estrai.query_joins import QueryJoinRenderer
from app.core.projects.canvas_models import Edge, Node


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
]


# ---------------------------------------------------------------------------
# helpers di costruzione
# ---------------------------------------------------------------------------

def _node(node_id: str, table_name: str, alias: str = None) -> Node:
    return Node(
        id=node_id,
        type="source",
        position={"x": 0, "y": 0},
        table_name=table_name,
        alias=alias,
    )


def _renderer(db_type: str = "postgres", db_schema: str = None) -> QueryJoinRenderer:
    gen = QueryGenerator(db_type=db_type)
    # Lo schema viene normalmente impostato da generate_sql; qui lo settiamo
    # esplicitamente per esercitare la qualificazione tabella.
    gen.db_schema = db_schema
    return QueryJoinRenderer(gen)


# ---------------------------------------------------------------------------
# costruzione / binding
# ---------------------------------------------------------------------------

def test_init_binds_generator_helpers_and_db_type():
    gen = QueryGenerator(db_type="mssql")
    r = QueryJoinRenderer(gen)
    assert r.db_type == "mssql"
    # I metodi di delega devono essere quelli del generator reale.
    assert r._generate_join == gen._genera_join
    assert r._generate_on_spatial == gen._genera_on_spaziale
    assert r._generate_on_attribute == gen._genera_on_attributivo


# ---------------------------------------------------------------------------
# generate_from
# ---------------------------------------------------------------------------

def test_generate_from_empty_raises():
    r = _renderer()
    with pytest.raises(ValueError, match="Nessuna tabella selezionata"):
        r.generate_from([], [])


def test_generate_from_single_table_no_alias():
    r = _renderer()
    out = r.generate_from([_node("n1", "clienti")], [])
    assert out == "clienti"


def test_generate_from_single_table_with_alias():
    r = _renderer()
    out = r.generate_from([_node("n1", "clienti", alias="c")], [])
    assert out == "clienti c"


def test_generate_from_qualifies_with_schema_for_postgres():
    r = _renderer(db_type="postgres", db_schema="public")
    out = r.generate_from([_node("n1", "clienti")], [])
    assert out == "public.clienti"


def test_generate_from_does_not_qualify_for_sqlite():
    # SQLite non e' schema-aware: lo schema viene ignorato.
    r = _renderer(db_type="sqlite", db_schema="public")
    out = r.generate_from([_node("n1", "clienti")], [])
    assert out == "clienti"


def test_generate_from_invalid_alias_raises():
    r = _renderer()
    bad = _node("n1", "clienti", alias="c; DROP TABLE x")
    with pytest.raises(ValueError, match="Alias tabella FROM"):
        r.generate_from([bad], [])


def test_generate_from_two_tables_appends_join():
    r = _renderer()
    nodes = [_node("n1", "clienti", "c"), _node("n2", "ordini", "o")]
    edge = Edge(
        id="e1", source="n1", target="n2",
        join_type="inner", source_column="id", target_column="cliente_id",
    )
    out = r.generate_from(nodes, [edge])
    lines = out.split("\n")
    assert lines[0] == "clienti c"
    assert lines[1].startswith("INNER JOIN ordini o ON ")
    assert "c.id = o.cliente_id" in lines[1]


# ---------------------------------------------------------------------------
# generate_join: scelta edge / errori
# ---------------------------------------------------------------------------

def test_generate_join_no_edge_raises_cartesian_guard():
    r = _renderer()
    nodes = [_node("n1", "a"), _node("n2", "b")]
    with pytest.raises(ValueError, match="non ha alcun edge di relazione"):
        r.generate_join(nodes[1], [], nodes, processed_ids={"n1"})


def test_generate_join_attribute_basic_left_default():
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    # join_type None -> default LEFT
    edge = Edge(id="e1", source="n1", target="n2", join_type=None,
                source_column="x", target_column="y")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
    assert out.startswith("LEFT JOIN b b ON ")
    assert "a.x = b.y" in out


def test_generate_join_reversed_swaps_columns():
    # L'edge punta dal nodo che stiamo joinando verso uno gia' processato:
    # is_reversed -> i ruoli source/target vengono scambiati.
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    edge = Edge(id="e1", source="n2", target="n1", join_type="inner",
                source_column="x", target_column="y")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
    # n2 e' source dell'edge ma e' il nodo da joinare -> reversed:
    # le colonne legacy source/target vengono scambiate prima dell'emissione.
    assert "a.y = b.x" in out


def test_generate_join_unknown_join_type_falls_back_to_left():
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="left",
                source_column="x", target_column="y")
    # forziamo un join_type non in whitelist bypassando la validazione del modello
    object.__setattr__(edge, "join_type", "BOGUS")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
    assert out.startswith("LEFT JOIN ")


def test_generate_join_full_and_outer_normalize_to_full_outer():
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    for jt in ("full", "outer"):
        edge = Edge(id="e1", source="n1", target="n2", join_type=jt,
                    source_column="x", target_column="y")
        out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
        assert out.startswith("FULL OUTER JOIN b b ON ")


def test_generate_join_cross_join_no_on_clause():
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="cross")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
    assert out == "CROSS JOIN b b"
    assert " ON " not in out


def test_generate_join_cross_join_invalid_alias_raises():
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", alias="b; DROP")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="cross")
    with pytest.raises(ValueError, match="Alias tabella CROSS JOIN"):
        r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})


def test_generate_join_alias_fallback_to_table_name():
    # Senza alias, l'alias usato nelle condizioni e' il nome tabella.
    r = _renderer()
    nodes = [_node("n1", "a"), _node("n2", "b")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                source_column="x", target_column="y")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
    assert "a.x = b.y" in out
    # nessun alias di tabella aggiunto dopo il nome
    assert out.startswith("INNER JOIN b ON ")


def test_generate_join_invalid_join_alias_raises():
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", alias="b-bad space")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                source_column="x", target_column="y")
    with pytest.raises(ValueError, match="Alias tabella JOIN"):
        r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})


def test_generate_join_picks_edge_without_processed_ids():
    # Senza processed_ids, sceglie l'edge in cui node e' target.
    r = _renderer()
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                source_column="x", target_column="y")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids=None)
    assert "a.x = b.y" in out


# ---------------------------------------------------------------------------
# generate_on_attribute: join_conditions / on_filters / errori
# ---------------------------------------------------------------------------

def test_generate_on_attribute_no_conditions_raises():
    r = _renderer()
    edge = Edge(id="e1", source="n1", target="n2", join_type="left")
    node = _node("n2", "b", "b")
    with pytest.raises(ValueError, match="JOIN senza condizioni"):
        r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=False)


def test_generate_on_attribute_multi_join_conditions_with_funcs():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="inner",
        join_conditions=[
            {"source_column": "k1", "target_column": "k2",
             "source_func": "UPPER", "target_func": "TRIM"},
            {"source_column": "c1", "target_column": "c2"},
        ],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "INNER", is_reversed=False)
    assert "UPPER(a.k1) = TRIM(b.k2)" in out
    assert "a.c1 = b.c2" in out
    assert " AND " in out


def test_generate_on_attribute_reversed_swaps_columns_and_funcs():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="inner",
        join_conditions=[
            {"source_column": "s", "target_column": "t",
             "source_func": "UPPER", "target_func": "LOWER"},
        ],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "INNER", is_reversed=True)
    # reversed: src_col<-target_column(t), tgt_col<-source_column(s),
    # src_func<-target_func(LOWER), tgt_func<-source_func(UPPER)
    assert "LOWER(a.t) = UPPER(b.s)" in out


def test_generate_on_attribute_skips_incomplete_conditions():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="inner",
        join_conditions=[
            {"source_column": "k", "target_column": ""},  # scartata
            {"source_column": "ok1", "target_column": "ok2"},
        ],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "INNER", is_reversed=False)
    assert out == "a.ok1 = b.ok2"


def test_generate_on_attribute_invalid_join_column_raises():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="inner",
        join_conditions=[{"source_column": "1bad", "target_column": "ok"}],
    )
    node = _node("n2", "b", "b")
    with pytest.raises(ValueError, match="Colonna JOIN non valida"):
        r.generate_on_attribute(edge, "a", "b", node, "INNER", is_reversed=False)


def test_generate_on_attribute_legacy_source_target_columns():
    r = _renderer()
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                source_column="src", target_column="tgt")
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "INNER", is_reversed=False)
    assert out == "a.src = b.tgt"


def test_generate_on_attribute_on_filters_operators():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="left",
        source_column="x", target_column="y",
        on_filters=[
            {"column": "stato", "operator": "=", "value": "attivo"},
            {"column": "deleted", "operator": "IS NULL", "value": ""},
            {"column": "tipo", "operator": "IN", "value": "a, b, c"},
        ],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=False)
    assert "a.x = b.y" in out
    # filtri applicati sul table_alias (b) quando non reversed
    assert "b.stato = 'attivo'" in out
    assert "b.deleted IS NULL" in out
    assert "b.tipo IN ('a', 'b', 'c')" in out


def test_generate_on_attribute_on_filter_value_quote_escaped():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="left",
        source_column="x", target_column="y",
        on_filters=[{"column": "nome", "operator": "=", "value": "O'Brien"}],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=False)
    assert "b.nome = 'O''Brien'" in out


def test_generate_on_attribute_on_filter_reversed_uses_source_alias():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="left",
        source_column="x", target_column="y",
        on_filters=[{"column": "stato", "operator": "=", "value": "v"}],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=True)
    # reversed -> filtro sul source_alias (a)
    assert "a.stato = 'v'" in out


def test_generate_on_attribute_invalid_on_operator_raises():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="left",
        source_column="x", target_column="y",
        on_filters=[{"column": "c", "operator": "DROP", "value": "v"}],
    )
    node = _node("n2", "b", "b")
    with pytest.raises(ValueError, match="Operatore ON filter non valido"):
        r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=False)


def test_generate_on_attribute_invalid_filter_column_raises():
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="left",
        source_column="x", target_column="y",
        on_filters=[{"column": "bad col", "operator": "=", "value": "v"}],
    )
    node = _node("n2", "b", "b")
    with pytest.raises(ValueError, match="Colonna filtro ON non valida"):
        r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=False)


def test_generate_on_attribute_empty_filter_column_skipped():
    # Filtro con column vuota viene saltato (continue), restano le colonne join.
    r = _renderer()
    edge = Edge(
        id="e1", source="n1", target="n2", join_type="left",
        source_column="x", target_column="y",
        on_filters=[{"column": "", "operator": "=", "value": "v"}],
    )
    node = _node("n2", "b", "b")
    out = r.generate_on_attribute(edge, "a", "b", node, "LEFT", is_reversed=False)
    assert out == "a.x = b.y"


# ---------------------------------------------------------------------------
# generate_on_spatial
# ---------------------------------------------------------------------------

def test_generate_on_spatial_intersects_default():
    r = _renderer(db_type="postgres")
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                is_spatial=True, spatial_predicate="ST_Intersects",
                source_geom_column="geom", target_geom_column="geom")
    out = r.generate_on_spatial(edge, "a", "b")
    assert "a.geom" in out
    assert "b.geom" in out
    # postgres usa la funzione ST_Intersects
    assert "Intersects" in out


def test_generate_on_spatial_dwithin_includes_distance():
    r = _renderer(db_type="postgres")
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                is_spatial=True, spatial_predicate="ST_DWithin",
                source_geom_column="geom", target_geom_column="geom",
                spatial_distance=150.0)
    out = r.generate_on_spatial(edge, "a", "b")
    assert "a.geom" in out and "b.geom" in out
    assert "150" in out


def test_generate_on_spatial_invalid_source_geom_raises():
    r = _renderer()
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                is_spatial=True, spatial_predicate="ST_Intersects",
                source_geom_column="bad geom", target_geom_column="geom")
    with pytest.raises(ValueError, match="Colonna geometria non valida"):
        r.generate_on_spatial(edge, "a", "b")


def test_generate_on_spatial_invalid_target_geom_raises():
    r = _renderer()
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                is_spatial=True, spatial_predicate="ST_Intersects",
                source_geom_column="geom", target_geom_column="bad geom")
    with pytest.raises(ValueError, match="Colonna geometria non valida"):
        r.generate_on_spatial(edge, "a", "b")


def test_generate_join_spatial_path_routes_to_spatial_on():
    # Edge spaziale: generate_join deve produrre una ON spaziale, non attributiva.
    r = _renderer(db_type="postgres")
    nodes = [_node("n1", "a", "a"), _node("n2", "b", "b")]
    edge = Edge(id="e1", source="n1", target="n2", join_type="inner",
                is_spatial=True, spatial_predicate="ST_Intersects",
                source_geom_column="geom", target_geom_column="geom")
    out = r.generate_join(nodes[1], [edge], nodes, processed_ids={"n1"})
    assert out.startswith("INNER JOIN b b ON ")
    assert "Intersects" in out
    assert "a.geom" in out and "b.geom" in out
