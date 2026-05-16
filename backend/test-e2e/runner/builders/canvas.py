"""Builder fluent per payload `CanvasWithPlugins`.

Costruisce il body di `PUT /api/projects/{id}/canvas` componendo i
nodi del canvas plenora con la stessa shape di
`app/core/projects/canvas_models.py::CanvasState` (esteso da
`router.py::CanvasWithPlugins`):

    {
        "nodes":   [Node, ...],
        "edges":   [Edge, ...],
        "plugins": {...}            # opzionale, ignorato lato server
    }

Ogni `Node` e' tipizzato dal field ``type`` (``source``, ``transform``,
``api_enrich``, ``carica``, ``result``, ...) con un sotto-set di
campi opzionali. Per i canvas di test usiamo:

    * ``source``       -> ESTRAI da DB (con ``connection_id``,
                          ``table_name``, ``where``)
    * ``api_enrich``   -> ESTRAI/ARRICCHISCI da REST (con ``api_id``)
    * ``file_source``  -> ESTRAI da datasource file
    * ``transform``    -> filter/join/aggregate/formula
                          (``transform_op`` Literal del model)
    * ``carica``       -> CARICA su DB di destinazione
    * ``result``       -> sink finale (per carica REST/file)

I node id sono UUID4 troncati (8 hex). Le edge sono create
automaticamente quando un `with_*` riceve un ``source_node_id``
che referenzia un node gia' presente.

Pure function: nessuna I/O, idempotente. ``build()`` deep-copia
le strutture interne, quindi e' sicuro chiamarlo piu' volte.
"""
from __future__ import annotations

import copy
from typing import Optional
from uuid import uuid4


def _new_id(prefix: str = "n") -> str:
    """Genera un id univoco per node/edge (`prefix_<8hex>`)."""
    return f"{prefix}_{uuid4().hex[:8]}"


class CanvasBuilder:
    """Costruisce un payload `CanvasWithPlugins` (fluent API).

    Esempio:
        >>> body = (
        ...     CanvasBuilder()
        ...         .with_estrai_sql(connection_id="c1", table="customers")
        ...         .with_filter(source_node_id="<id>", where="age > 18")
        ...         .build()
        ... )
        >>> sorted(body) == ["edges", "nodes", "plugins"]
        True
    """

    def __init__(self) -> None:
        self._nodes: list[dict] = []
        self._edges: list[dict] = []
        self._plugins: dict = {}
        # x-coord auto-incrementing per layout deterministico (no ovelapping nodi)
        self._next_x: float = 0.0

    # ------------------------------------------------------------------
    # helpers interni
    # ------------------------------------------------------------------

    def _next_position(self) -> dict:
        """Ritorna una posizione (x, y) progressiva per il prossimo nodo."""
        x = self._next_x
        self._next_x += 200.0
        return {"x": x, "y": 0.0}

    def _has_node(self, node_id: str) -> bool:
        return any(n["id"] == node_id for n in self._nodes)

    def _add_node(self, node: dict) -> str:
        """Append node + return its id. Assume ``node['id']`` gia' settato."""
        self._nodes.append(node)
        return node["id"]

    def _add_edge(
        self,
        *,
        source: str,
        target: str,
        join_type: str = "left",
        source_column: str = "",
        target_column: str = "",
        extra: Optional[dict] = None,
    ) -> str:
        """Append edge (source -> target) + return its id."""
        edge_id = _new_id("e")
        edge: dict = {
            "id": edge_id,
            "source": source,
            "target": target,
            "join_type": join_type,
            "source_column": source_column,
            "target_column": target_column,
        }
        if extra:
            edge.update(extra)
        self._edges.append(edge)
        return edge_id

    def _link_if_present(
        self,
        source_node_id: Optional[str],
        target_node_id: str,
        *,
        join_type: str = "left",
    ) -> None:
        """Crea edge ``source -> target`` solo se source e' nel canvas.

        Se ``source_node_id`` e' None o non corrisponde a nessun nodo
        registrato (es. l'utente passa un id custom non builder-managed),
        skippa silenziosamente: e' compito del chiamante essere coerente.
        """
        if source_node_id and self._has_node(source_node_id):
            self._add_edge(source=source_node_id, target=target_node_id, join_type=join_type)

    # ------------------------------------------------------------------
    # ESTRAI: sorgenti dati
    # ------------------------------------------------------------------

    def with_estrai_sql(
        self,
        *,
        connection_id: str,
        table: str,
        where: Optional[str] = None,
        alias: Optional[str] = None,
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``source`` (ESTRAI SQL/database).

        Args:
            connection_id: id della Connection (POST /api/connections).
            table: nome tabella nel DB sorgente.
            where: filtro WHERE (raw SQL, opzionale).
            alias: alias del nodo (default: ``table``).
        """
        node_id = _new_id("src")
        node: dict = {
            "id": node_id,
            "type": "source",
            "position": self._next_position(),
            "connection_id": connection_id,
            "table_name": table,
            "alias": alias or table,
        }
        if where is not None:
            node["where"] = where
        self._add_node(node)
        return self

    def with_estrai_rest(
        self,
        *,
        api_connection_id: str,
        endpoint: str,
        json_path: Optional[str] = None,
        method: str = "GET",
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``api_enrich`` come fonte REST.

        Args:
            api_connection_id: id della api_connection.
            endpoint: path relativo (concat alla ``base_url`` della
                api_connection) oppure URL assoluto.
            json_path: jsonpath per estrarre la collezione di record
                dalla response (es. ``"$.results"``).
            method: HTTP method (default GET).
        """
        node_id = _new_id("rest")
        node: dict = {
            "id": node_id,
            "type": "api_enrich",
            "position": self._next_position(),
            "api_id": api_connection_id,
            "endpoint": {"url": endpoint, "method": method},
        }
        if json_path is not None:
            node["response"] = {"json_path": json_path}
        self._add_node(node)
        return self

    def with_estrai_file(
        self,
        *,
        datasource_id: str,
        path: str,
        file_type: str,
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``file_source`` (ESTRAI da datasource file).

        Args:
            datasource_id: id del datasource (FTP/SFTP/local/...).
            path: path relativo dentro al datasource.
            file_type: ``"csv"`` | ``"xlsx"`` | ``"geojson"`` | ``"gpkg"`` | ...
        """
        node_id = _new_id("file")
        node: dict = {
            "id": node_id,
            "type": "file_source",
            "position": self._next_position(),
            "datasource_id": datasource_id,
            "file_path": path,
            "file_mode": "fixed",
            "parse_options": {"file_type": file_type},
        }
        self._add_node(node)
        return self

    # ------------------------------------------------------------------
    # MANIPOLA: trasformazioni
    # ------------------------------------------------------------------

    def with_filter(
        self,
        *,
        source_node_id: str,
        where: str,
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``transform`` con ``transform_op="filter"``.

        L'edge ``source_node_id -> filter`` viene creato se source e'
        gia' nel canvas.
        """
        node_id = _new_id("flt")
        node: dict = {
            "id": node_id,
            "type": "transform",
            "transform_op": "filter",
            "transform_category": "base",
            "position": self._next_position(),
            "config": {"where": where},
        }
        self._add_node(node)
        self._link_if_present(source_node_id, node_id, join_type="left")
        return self

    def with_join(
        self,
        *,
        left_node_id: str,
        right_node_id: str,
        kind: str = "inner",
        on: list[tuple[str, str]],
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``transform`` con ``transform_op="join"``.

        Args:
            left_node_id, right_node_id: id dei due nodi sorgente.
            kind: ``"inner"`` | ``"left"`` | ``"right"`` | ``"outer"``.
            on: lista di coppie ``(left_col, right_col)`` per il join.

        Crea due edge: ``left -> join`` e ``right -> join`` (entrambi
        solo se gli id risultano nel canvas).
        """
        node_id = _new_id("jn")
        join_conditions = [
            {"left": lc, "right": rc, "operator": "="} for lc, rc in on
        ]
        node: dict = {
            "id": node_id,
            "type": "transform",
            "transform_op": "join",
            "transform_category": "base",
            "position": self._next_position(),
            "config": {"kind": kind, "on": join_conditions},
        }
        self._add_node(node)
        self._link_if_present(left_node_id, node_id, join_type=kind)
        self._link_if_present(right_node_id, node_id, join_type=kind)
        return self

    def with_aggregate(
        self,
        *,
        source_node_id: str,
        group_by: list[str],
        aggregations: dict,
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``transform`` con ``transform_op="aggregate"``.

        Args:
            group_by: colonne di GROUP BY.
            aggregations: dict ``{"col": "FUNC"}`` (es.
                ``{"total_amount": "SUM", "id": "COUNT"}``).
        """
        node_id = _new_id("agg")
        agg_list = [
            {"column": col, "function": fn.upper()}
            for col, fn in aggregations.items()
        ]
        node: dict = {
            "id": node_id,
            "type": "transform",
            "transform_op": "aggregate",
            "transform_category": "base",
            "position": self._next_position(),
            "group_by_enabled": True,
            "group_by_columns": list(group_by),
            "aggregations": agg_list,
        }
        self._add_node(node)
        self._link_if_present(source_node_id, node_id, join_type="left")
        return self

    def with_formula(
        self,
        *,
        source_node_id: str,
        column: str,
        formula: str,
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``transform`` con ``transform_op="formula"``.

        Args:
            column: nome della colonna calcolata di output.
            formula: espressione (sintassi plenora-formula).
        """
        node_id = _new_id("fml")
        node: dict = {
            "id": node_id,
            "type": "transform",
            "transform_op": "formula",
            "transform_category": "base",
            "position": self._next_position(),
            "calculated_columns": [{"name": column, "formula": formula}],
        }
        self._add_node(node)
        self._link_if_present(source_node_id, node_id, join_type="left")
        return self

    # ------------------------------------------------------------------
    # ARRICCHISCI: arricchimento via REST
    # ------------------------------------------------------------------

    def with_arricchisci_rest(
        self,
        *,
        source_node_id: str,
        api_connection_id: str,
        mapping: dict,
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``api_enrich`` di arricchimento.

        A differenza di :meth:`with_estrai_rest` (sorgente standalone),
        questo prende dati upstream e li arricchisce via API.

        Args:
            mapping: dict ``{request_param: source_column}``. Per ogni
                riga upstream, il valore di ``source_column`` viene
                passato come ``request_param`` alla request.
        """
        node_id = _new_id("enr")
        param_mapping = [
            {"param": p, "source_column": col} for p, col in mapping.items()
        ]
        node: dict = {
            "id": node_id,
            "type": "api_enrich",
            "position": self._next_position(),
            "api_id": api_connection_id,
            "param_mapping": param_mapping,
        }
        self._add_node(node)
        self._link_if_present(source_node_id, node_id, join_type="left")
        return self

    # ------------------------------------------------------------------
    # CARICA: sink
    # ------------------------------------------------------------------

    def with_carica_sql(
        self,
        *,
        source_node_id: str,
        connection_id: str,
        target_table: str,
        mode: str = "append",
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``carica`` (CARICA su DB).

        Args:
            connection_id: id della Connection di destinazione.
            target_table: nome tabella destinazione.
            mode: ``"create"`` | ``"replace"`` | ``"append"`` | ``"upsert"``
                | ``"update"`` | ``"truncate_insert"``.
        """
        node_id = _new_id("car")
        node: dict = {
            "id": node_id,
            "type": "carica",
            "position": self._next_position(),
            "dest_type": "db",
            "connection_id": connection_id,
            "table_name": target_table,
            "write_mode": mode,
        }
        self._add_node(node)
        self._link_if_present(source_node_id, node_id, join_type="carica")
        return self

    def with_carica_rest_sink(
        self,
        *,
        source_node_id: str,
        api_connection_id: str,
        endpoint: str,
        method: str = "POST",
    ) -> "CanvasBuilder":
        """Aggiunge un nodo ``result`` come sink REST (carica via API).

        In plenora i sink REST sono modellati come ``result`` a valle
        di un ``api_enrich`` configurato come writer; qui esponiamo
        una factory comoda che crea direttamente il sink.
        """
        node_id = _new_id("snk")
        node: dict = {
            "id": node_id,
            "type": "result",
            "position": self._next_position(),
            "api_id": api_connection_id,
            "endpoint": {"url": endpoint, "method": method},
        }
        self._add_node(node)
        self._link_if_present(source_node_id, node_id, join_type="output")
        return self

    # ------------------------------------------------------------------
    # build / introspection
    # ------------------------------------------------------------------

    def with_plugins(self, plugins: dict) -> "CanvasBuilder":
        """Setta il dict ``plugins`` (passato as-is al server, ignorato lato BE).

        Esposto principalmente per i test che vogliono verificare il
        round-trip del campo opzionale ``plugins`` di
        ``CanvasWithPlugins``.
        """
        self._plugins = dict(plugins)
        return self

    def last_node_id(self) -> Optional[str]:
        """Ritorna l'id dell'ultimo nodo aggiunto (utile per chaining).

        Esempio:
            >>> b = CanvasBuilder().with_estrai_sql(connection_id="c", table="t")
            >>> src = b.last_node_id()
            >>> _ = b.with_filter(source_node_id=src, where="x > 0")
        """
        return self._nodes[-1]["id"] if self._nodes else None

    def build(self) -> dict:
        """Ritorna il dict ``{nodes, edges, plugins}``.

        Idempotente: chiamate multiple ritornano lo stesso payload.
        Le strutture interne vengono deep-copiate per evitare che il
        chiamante muti accidentalmente lo stato del builder.
        """
        return {
            "nodes": copy.deepcopy(self._nodes),
            "edges": copy.deepcopy(self._edges),
            "plugins": copy.deepcopy(self._plugins),
        }
