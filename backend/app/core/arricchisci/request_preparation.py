"""HTTP and response configuration preparation, shared with preflight."""
from app.shared.contracts.payloads import JsonObject
from app.core.arricchisci.http_executor import validate_request_spec_config
from app.core.arricchisci.response_parser import csv_dialect_from_config, response_format_from_config


def prepare_api_request(api: JsonObject) -> None:
    """Metodo HTTP/body e dialetto CSV con i validatori del runtime. Il runtime
    li applica per richiesta (RowError per riga); Valida e preflight chiamano
    questa funzione e ne fanno un errore di configurazione."""
    endpoint = api.get("endpoint") or {}
    request = api.get("request") or {}
    validate_request_spec_config(endpoint.get("method"), request.get("body_type", "json"))
    if response_format_from_config(api) == "csv":
        csv_dialect_from_config(api)


def prepare_generator_request(api: JsonObject) -> None:
    """Gate statici del generator eseguiti dal runtime prima della prima
    richiesta: esattamente quelli di HEAD (URL, async+paginazione)."""
    if not (api.get("endpoint") or {}).get("url"):
        raise ValueError("URL API non configurato")
    if (api.get("async") or {}).get("enabled") and (api.get("pagination") or {}).get("type"):
        raise ValueError("API generator: async e pagination non sono supportati insieme")


def prepare_generator_preflight(api: JsonObject) -> None:
    """Valida/preflight del generator: gate del runtime più i controlli che il
    runtime applica alla prima richiesta (metodo/body, dialetto CSV,
    paginazione). Più severa del runtime solo come Valida: il runtime non la
    chiama, così l'esecuzione resta quella di HEAD."""
    prepare_generator_request(api)
    prepare_api_request(api)
    prepare_generator_pagination(api.get("pagination") or {})


def prepare_generator_pagination(pagination: JsonObject) -> tuple[int, int]:
    """Same numeric parsing used by the page loop; no remote page prediction."""
    if not pagination.get("type"):
        return 100, 10000
    page_size = int(pagination.get("page_size", 100))
    max_rows = int(pagination.get("max_rows", 10000))
    if pagination["type"] == "offset":
        int(pagination.get("start_page", 0))
    elif pagination["type"] in ("page", "page_number", "page-number"):
        int(pagination.get("start_page", 1))
    return page_size, max_rows
