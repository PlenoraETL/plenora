"""Famiglie dei tipi per la tabella delle aggregazioni (Blocco A A1, terzo giro).

Ogni tipo che circola nelle colonne previste deve trovare la sua famiglia:
tipi canonici, tipi logici del contratto di uscita, alias dei database e
sottotipi delle geometrie. Senza la regola, un project_source con tipi
"integer"/"number" rendeva incerto lo schema di un Output con group by.
"""
import pytest

from app.core.output_contract import service as contract_service
from app.core.resources.column_types import CANONICAL_BASE_TYPES, TYPE_ALIASES
from app.shared.execution.schema_column_plans import (
    AGGREGATION_TYPES,
    aggregation_output_type,
    type_family,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

FAMILIES = set(next(iter(AGGREGATION_TYPES.values())))
CONTRACT_RAW = [
    "str", "string", "text", "object", "int", "int64", "integer", "bigint", "smallint",
    "real", "float", "float64", "double", "decimal", "numeric", "bool", "boolean",
    "date", "datetime", "timestamp", "geometry", "geometry:point:4326", "geometry:Polygon",
]
GEO_SUBTYPES = ["geometry:point", "geometry:Point:4326", "geometry:multipolygon:3003"]


@pytest.mark.parametrize("raw", sorted({*CANONICAL_BASE_TYPES, *TYPE_ALIASES, *CONTRACT_RAW, *GEO_SUBTYPES}))
def test_ogni_tipo_che_circola_ha_una_famiglia_in_tabella(raw):
    assert type_family(raw) in FAMILIES


@pytest.mark.parametrize("raw", CONTRACT_RAW)
def test_i_tipi_logici_del_contratto_hanno_una_famiglia(raw):
    logical = contract_service._logical_type(raw)
    assert type_family(logical) in FAMILIES


def test_famiglia_invariata_conserva_il_tipo_originale():
    assert aggregation_output_type("sum", "number") == "number"
    assert aggregation_output_type("first", "geometry:point:4326") == "geometry:point:4326"
    assert aggregation_output_type("sum", "integer") == "integer"


def test_famiglia_cambiata_usa_la_tabella():
    assert aggregation_output_type("mean", "integer") == "real"
    assert aggregation_output_type("count", "string") == "int"


def test_coppia_non_eseguibile_resta_rifiutata():
    with pytest.raises(TypeError):
        aggregation_output_type("mean", "string")


@pytest.mark.parametrize("raw", ["json", "uuid", "time", "category", "datetime64[ns]"])
def test_famiglia_sconosciuta_nessun_falso_errore(raw):
    assert aggregation_output_type("count", raw) == "int"
    assert aggregation_output_type("nunique", raw) == "int"
    assert aggregation_output_type("first", raw) == raw


def test_geometria_in_ogni_forma():
    for raw in ["geometry", "geometry:point:4326", "geometry(Point,4326)", "GEOMETRY"]:
        assert type_family(raw) == "geometry"


def test_nessun_confronto_a_mano_sui_tipi_nelle_previsioni():
    """Ogni confronto tra tipi passa da type_family (regola unica)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "app"
    files = [*root.glob("core/projects/schema_*.py"), root / "core/manipola/node_validator.py",
             root / "shared/execution/canvas_columns.py"]
    literal = re.compile(r"\.type\s*(not\s+)?in\s*[{\[(]\s*['\"]|\.type\s*[!=]=\s*['\"](int|real|str|bool|date|datetime|integer|number|string|boolean)['\"]")
    offenders = [f"{f.name}:{i}" for f in files for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                 if literal.search(line)]
    assert offenders == []
