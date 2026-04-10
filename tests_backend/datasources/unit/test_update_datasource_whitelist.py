"""
tests/datasources/unit/test_update_datasource_whitelist.py
==========================================================
Unit test per il fix HIGH su `update_datasource` in config_db_service.py.

Verifica che la whitelist `_ALLOWED_UPDATE_FIELDS["datasources"]` sia
applicata e che kwargs non consentiti vengano rifiutati con ValueError.
"""

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


# Override del fixture autouse mock_reset
@pytest.fixture(autouse=True)
def mock_reset():
    yield


class TestUpdateDatasourceWhitelist:

    def test_allowed_fields_pass(self):
        """name, ds_type, config, updated_at sono ammessi."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute") as mock_exec:
            update_datasource("ds-1", name="new-name", ds_type="local", config={"a": 1})
        # execute deve essere stato chiamato senza eccezioni
        assert mock_exec.called
        sql, params = mock_exec.call_args.args
        assert "UPDATE datasources SET" in sql
        # Tutti i campi consentiti compaiono nel SET
        assert "name = ?" in sql
        assert "ds_type = ?" in sql
        assert "config = ?" in sql

    def test_disallowed_field_group_id_rejected(self):
        """group_id non è in whitelist → ValueError."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute"):
            with pytest.raises(ValueError) as exc:
                update_datasource("ds-1", group_id="other-group")
        assert "group_id" in str(exc.value)
        assert "non consentit" in str(exc.value).lower()

    def test_disallowed_field_id_rejected(self):
        """id non è in whitelist → ValueError."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute"):
            with pytest.raises(ValueError):
                update_datasource("ds-1", id="hijacked")

    def test_disallowed_field_created_by_rejected(self):
        """created_by non è in whitelist → ValueError."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute"):
            with pytest.raises(ValueError):
                update_datasource("ds-1", created_by="other-user")

    def test_config_dict_serialized_to_json(self):
        """Se config è dict, viene serializzato a JSON prima dell'UPDATE."""
        from app.db.config_db_service import update_datasource
        import json
        with patch("app.db.config_db_service.execute") as mock_exec:
            update_datasource("ds-1", config={"host": "x", "port": 21})
        params = mock_exec.call_args.args[1]
        # Trova il parametro config — è una stringa JSON
        json_param = next((p for p in params if isinstance(p, str) and p.startswith("{")), None)
        assert json_param is not None
        parsed = json.loads(json_param)
        assert parsed["host"] == "x"
        assert parsed["port"] == 21

    def test_updated_at_auto_added(self):
        """updated_at viene impostato automaticamente lato server."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute") as mock_exec:
            update_datasource("ds-1", config={"x": 1})
        sql = mock_exec.call_args.args[0]
        assert "updated_at = ?" in sql

    def test_empty_kwargs_no_op(self):
        """update_datasource() senza kwargs non tocca il DB."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute") as mock_exec:
            update_datasource("ds-1")
        assert not mock_exec.called

    def test_partial_disallowed_rejects_all(self):
        """Anche un solo campo non consentito blocca l'intera UPDATE."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute") as mock_exec:
            with pytest.raises(ValueError):
                update_datasource("ds-1", name="ok", group_id="bad")
        # Nessuna chiamata a execute (fallimento prima)
        assert not mock_exec.called

    def test_ds_id_in_where_clause(self):
        """L'ds_id viene passato come parametro WHERE, non nei SET."""
        from app.db.config_db_service import update_datasource
        with patch("app.db.config_db_service.execute") as mock_exec:
            update_datasource("ds-uuid-xyz", config={"x": 1})
        sql, params = mock_exec.call_args.args
        assert "WHERE id = ?" in sql
        assert params[-1] == "ds-uuid-xyz"
