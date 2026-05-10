"""Unit test per i test data builder (smoke, no infra).

Verificano:
    * shape dei payload prodotti dai builder;
    * presenza dei campi obbligatori dei contratti plenora;
    * idempotenza di ``CanvasBuilder.build()``;
    * generazione automatica di edge quando il ``source_node_id`` e'
      gia' nel canvas.

Marker pytest: ``smoke`` (no app, no db, no I/O).
"""
from __future__ import annotations

import pytest

from . import (
    ApiConnectionBuilder,
    CanvasBuilder,
    ConnectionBuilder,
    DatasourceBuilder,
)


pytestmark = pytest.mark.smoke


# ── ConnectionBuilder ───────────────────────────────────────────────────

class TestConnectionBuilder:
    """Le factory ConnectionBuilder ritornano payload `ConnectionCreate`-compatibili."""

    REQUIRED = {"name", "type", "host", "port", "database", "username", "password"}

    def test_postgres_has_required_fields(self) -> None:
        payload = ConnectionBuilder.postgres()
        assert self.REQUIRED.issubset(payload), (
            f"Campi mancanti: {self.REQUIRED - payload.keys()}"
        )
        assert payload["type"] == "postgres"
        assert payload["port"] == 5432
        assert payload["host"] == "plenora-e2e-postgres"

    def test_mysql_default_port(self) -> None:
        payload = ConnectionBuilder.mysql()
        assert payload["type"] == "mysql"
        assert payload["port"] == 3306

    def test_mssql_uses_mssql_type_not_sqlserver(self) -> None:
        # plenora ConnectionCreate.type Literal["postgres","mssql","mysql","oracle","sqlite"]
        payload = ConnectionBuilder.mssql()
        assert payload["type"] == "mssql"
        assert payload["port"] == 1433

    def test_oracle_default_port(self) -> None:
        payload = ConnectionBuilder.oracle()
        assert payload["type"] == "oracle"
        assert payload["port"] == 1521

    def test_sqlite_path_maps_to_database(self) -> None:
        payload = ConnectionBuilder.sqlite(path="/tmp/test.db")
        assert payload["type"] == "sqlite"
        assert payload["database"] == "/tmp/test.db"

    def test_postgres_overrides(self) -> None:
        payload = ConnectionBuilder.postgres(
            host="custom-host", port=15432, database="other_db",
            username="u", password="p", name="my-conn",
        )
        assert payload["host"] == "custom-host"
        assert payload["port"] == 15432
        assert payload["database"] == "other_db"
        assert payload["username"] == "u"
        assert payload["password"] == "p"
        assert payload["name"] == "my-conn"


# ── ApiConnectionBuilder ────────────────────────────────────────────────

class TestApiConnectionBuilder:
    BASE_URL = "http://plenora-e2e-fake-rest:8080"

    def test_api_key_default_header_location(self) -> None:
        p = ApiConnectionBuilder.api_key(base_url=self.BASE_URL)
        assert p["auth_type"] == "api_key"
        assert p["key_location"] == "header"
        assert p["key_name"] == "X-API-Key"
        assert p["api_key"] == "PLENORA_TEST_KEY"
        assert p["base_url"] == self.BASE_URL
        assert p["connection_type"] == "rest"

    def test_api_key_query_location(self) -> None:
        p = ApiConnectionBuilder.api_key(
            base_url=self.BASE_URL, location="query", header="api_key",
        )
        assert p["key_location"] == "query"
        assert p["key_name"] == "api_key"

    def test_bearer(self) -> None:
        p = ApiConnectionBuilder.bearer(base_url=self.BASE_URL, token="abc123")
        assert p["auth_type"] == "bearer"
        assert p["token"] == "abc123"

    def test_basic(self) -> None:
        p = ApiConnectionBuilder.basic(
            base_url=self.BASE_URL, username="u", password="p",
        )
        assert p["auth_type"] == "basic"
        assert p["username"] == "u"
        assert p["password"] == "p"

    def test_oauth2_client_credentials(self) -> None:
        p = ApiConnectionBuilder.oauth2_client_credentials(
            base_url=self.BASE_URL,
            token_url=f"{self.BASE_URL}/auth/oauth2/token",
        )
        assert p["auth_type"] == "oauth2_client_credentials"
        assert p["token_url"].endswith("/auth/oauth2/token")
        assert p["client_id"] == "plenora"
        assert p["client_secret"] == "plenora_secret"

    def test_oauth2_password(self) -> None:
        p = ApiConnectionBuilder.oauth2_password(
            base_url=self.BASE_URL,
            token_url=f"{self.BASE_URL}/auth/oauth2/token",
            username="u", password="p",
        )
        assert p["auth_type"] == "oauth2_password"
        assert p["username"] == "u"
        assert p["password"] == "p"

    def test_arcgis_token(self) -> None:
        p = ApiConnectionBuilder.arcgis_token(
            base_url=self.BASE_URL,
            token_url=f"{self.BASE_URL}/auth/arcgis/generateToken",
        )
        assert p["auth_type"] == "arcgis_token"
        assert p["connection_type"] == "arcgis"


# ── DatasourceBuilder ───────────────────────────────────────────────────

class TestDatasourceBuilder:
    def test_ftp_defaults(self) -> None:
        p = DatasourceBuilder.ftp()
        assert p["type"] == "ftp"
        assert p["host"] == "plenora-e2e-ftp"
        assert p["port"] == 21
        assert p["passive"] is True
        assert p["tls"] is False

    def test_sftp_defaults(self) -> None:
        p = DatasourceBuilder.sftp()
        assert p["type"] == "sftp"
        assert p["host"] == "plenora-e2e-sftp"
        assert p["port"] == 22
        assert p["host_key_policy"] == "auto_add"
        # private_key non viene incluso se None
        assert "private_key" not in p

    def test_sftp_with_private_key(self) -> None:
        p = DatasourceBuilder.sftp(private_key="-----BEGIN PRIVATE KEY-----\n...")
        assert "private_key" in p
        assert p["private_key"].startswith("-----BEGIN PRIVATE KEY-----")

    def test_local_only_root_path(self) -> None:
        p = DatasourceBuilder.local(root_path="/srv/data")
        assert p["type"] == "local"
        assert p["root_path"] == "/srv/data"


# ── CanvasBuilder ────────────────────────────────────────────────────────

class TestCanvasBuilder:
    """Test sui canvas-builder: shape, edges automatici, idempotenza."""

    def test_empty_build(self) -> None:
        body = CanvasBuilder().build()
        assert body == {"nodes": [], "edges": [], "plugins": {}}

    def test_estrai_sql_creates_source_node(self) -> None:
        body = CanvasBuilder().with_estrai_sql(
            connection_id="c1", table="customers",
        ).build()
        assert len(body["nodes"]) == 1
        n = body["nodes"][0]
        assert n["type"] == "source"
        assert n["connection_id"] == "c1"
        assert n["table_name"] == "customers"
        assert n["alias"] == "customers"
        # Una source standalone non deve generare edge
        assert body["edges"] == []
        # Position e' presente e tipizzata come dict {x, y}
        assert set(n["position"].keys()) == {"x", "y"}

    def test_estrai_sql_with_where_and_alias(self) -> None:
        body = CanvasBuilder().with_estrai_sql(
            connection_id="c1", table="orders",
            where="status = 'paid'", alias="paid_orders",
        ).build()
        n = body["nodes"][0]
        assert n["where"] == "status = 'paid'"
        assert n["alias"] == "paid_orders"

    def test_filter_creates_edge_to_source(self) -> None:
        b = CanvasBuilder().with_estrai_sql(
            connection_id="c1", table="customers",
        )
        src_id = b.last_node_id()
        body = b.with_filter(source_node_id=src_id, where="age > 18").build()

        assert len(body["nodes"]) == 2
        assert len(body["edges"]) == 1
        edge = body["edges"][0]
        assert edge["source"] == src_id
        # target = filter node (last)
        assert edge["target"] == body["nodes"][1]["id"]

    def test_filter_skips_edge_if_source_not_in_canvas(self) -> None:
        """source_node_id sconosciuto: il nodo viene comunque creato,
        ma niente edge auto (chiamante responsabile)."""
        body = CanvasBuilder().with_filter(
            source_node_id="nonexistent", where="x > 0",
        ).build()
        assert len(body["nodes"]) == 1
        assert body["edges"] == []

    def test_join_creates_two_edges(self) -> None:
        b = (
            CanvasBuilder()
            .with_estrai_sql(connection_id="c1", table="customers")
        )
        left = b.last_node_id()
        b = b.with_estrai_sql(connection_id="c1", table="orders")
        right = b.last_node_id()
        body = b.with_join(
            left_node_id=left, right_node_id=right,
            kind="inner",
            on=[("id", "customer_id")],
        ).build()

        assert len(body["nodes"]) == 3
        # 2 edge: left->join e right->join
        assert len(body["edges"]) == 2
        sources = {e["source"] for e in body["edges"]}
        assert sources == {left, right}
        target = body["edges"][0]["target"]
        assert all(e["target"] == target for e in body["edges"])
        # transform_op del nodo join
        join_node = body["nodes"][2]
        assert join_node["transform_op"] == "join"
        assert join_node["config"]["kind"] == "inner"

    def test_aggregate_shape(self) -> None:
        b = CanvasBuilder().with_estrai_sql(
            connection_id="c1", table="orders",
        )
        src = b.last_node_id()
        body = b.with_aggregate(
            source_node_id=src,
            group_by=["customer_id"],
            aggregations={"total_amount": "SUM", "id": "COUNT"},
        ).build()
        agg = body["nodes"][1]
        assert agg["transform_op"] == "aggregate"
        assert agg["group_by_enabled"] is True
        assert agg["group_by_columns"] == ["customer_id"]
        # aggregations e' una list[{column, function}]
        funcs = {a["column"]: a["function"] for a in agg["aggregations"]}
        assert funcs == {"total_amount": "SUM", "id": "COUNT"}

    def test_formula_shape(self) -> None:
        b = CanvasBuilder().with_estrai_sql(connection_id="c1", table="t")
        src = b.last_node_id()
        body = b.with_formula(
            source_node_id=src, column="line_total",
            formula="qty * unit_price",
        ).build()
        fml = body["nodes"][1]
        assert fml["transform_op"] == "formula"
        cols = fml["calculated_columns"]
        assert cols == [{"name": "line_total", "formula": "qty * unit_price"}]

    def test_carica_sql_creates_carica_node_and_edge(self) -> None:
        b = CanvasBuilder().with_estrai_sql(connection_id="c1", table="src")
        src = b.last_node_id()
        body = b.with_carica_sql(
            source_node_id=src, connection_id="c2",
            target_table="dst", mode="append",
        ).build()
        car = body["nodes"][1]
        assert car["type"] == "carica"
        assert car["dest_type"] == "db"
        assert car["connection_id"] == "c2"
        assert car["table_name"] == "dst"
        assert car["write_mode"] == "append"
        assert len(body["edges"]) == 1
        assert body["edges"][0]["join_type"] == "carica"

    def test_estrai_rest_shape(self) -> None:
        body = CanvasBuilder().with_estrai_rest(
            api_connection_id="api1",
            endpoint="/customers",
            json_path="$.results",
        ).build()
        n = body["nodes"][0]
        assert n["type"] == "api_enrich"
        assert n["api_id"] == "api1"
        assert n["endpoint"] == {"url": "/customers", "method": "GET"}
        assert n["response"] == {"json_path": "$.results"}

    def test_estrai_file_shape(self) -> None:
        body = CanvasBuilder().with_estrai_file(
            datasource_id="ds1", path="data/customers.csv", file_type="csv",
        ).build()
        n = body["nodes"][0]
        assert n["type"] == "file_source"
        assert n["datasource_id"] == "ds1"
        assert n["file_path"] == "data/customers.csv"
        assert n["parse_options"]["file_type"] == "csv"

    def test_arricchisci_rest_mapping(self) -> None:
        b = CanvasBuilder().with_estrai_sql(connection_id="c", table="customers")
        src = b.last_node_id()
        body = b.with_arricchisci_rest(
            source_node_id=src, api_connection_id="api1",
            mapping={"city": "city_name", "country": "country_code"},
        ).build()
        enr = body["nodes"][1]
        assert enr["type"] == "api_enrich"
        params = {p["param"]: p["source_column"] for p in enr["param_mapping"]}
        assert params == {"city": "city_name", "country": "country_code"}

    def test_carica_rest_sink(self) -> None:
        b = CanvasBuilder().with_estrai_sql(connection_id="c", table="t")
        src = b.last_node_id()
        body = b.with_carica_rest_sink(
            source_node_id=src, api_connection_id="api1",
            endpoint="/upload", method="POST",
        ).build()
        snk = body["nodes"][1]
        assert snk["type"] == "result"
        assert snk["api_id"] == "api1"
        assert snk["endpoint"] == {"url": "/upload", "method": "POST"}

    def test_node_ids_are_unique(self) -> None:
        body = (
            CanvasBuilder()
            .with_estrai_sql(connection_id="c", table="a")
            .with_estrai_sql(connection_id="c", table="b")
            .with_estrai_sql(connection_id="c", table="c")
            .build()
        )
        ids = [n["id"] for n in body["nodes"]]
        assert len(ids) == len(set(ids)), f"id duplicati: {ids}"

    def test_build_idempotent(self) -> None:
        """Chiamare build() due volte ritorna lo stesso payload."""
        b = (
            CanvasBuilder()
            .with_estrai_sql(connection_id="c1", table="customers")
        )
        src = b.last_node_id()
        b = b.with_filter(source_node_id=src, where="age > 18")

        body1 = b.build()
        body2 = b.build()
        assert body1 == body2

    def test_build_returns_independent_copy(self) -> None:
        """Mutare il dict ritornato non deve sporcare lo stato del builder."""
        b = CanvasBuilder().with_estrai_sql(connection_id="c", table="t")
        body = b.build()
        body["nodes"].append({"id": "MUTATED"})
        body["edges"].append({"id": "MUTATED"})
        body["plugins"]["x"] = 1

        # Una nuova build() deve ritornare il payload originale, immutato
        body2 = b.build()
        assert len(body2["nodes"]) == 1
        assert body2["edges"] == []
        assert body2["plugins"] == {}

    def test_plugins_passthrough(self) -> None:
        body = CanvasBuilder().with_plugins({"foo": "bar"}).build()
        assert body["plugins"] == {"foo": "bar"}

    def test_full_pipeline_shape(self) -> None:
        """Pipeline completo ESTRAI -> FILTER -> AGGREGATE -> CARICA."""
        b = CanvasBuilder().with_estrai_sql(connection_id="c1", table="orders")
        src = b.last_node_id()
        b = b.with_filter(source_node_id=src, where="status = 'paid'")
        flt = b.last_node_id()
        b = b.with_aggregate(
            source_node_id=flt, group_by=["customer_id"],
            aggregations={"total_amount": "SUM"},
        )
        agg = b.last_node_id()
        b = b.with_carica_sql(
            source_node_id=agg, connection_id="c2",
            target_table="customer_revenue", mode="replace",
        )
        body = b.build()
        # 4 nodi: source, filter, aggregate, carica
        assert len(body["nodes"]) == 4
        # 3 edge: src->flt, flt->agg, agg->carica
        assert len(body["edges"]) == 3
        # Top-level shape conforme a CanvasWithPlugins
        assert sorted(body) == ["edges", "nodes", "plugins"]
