"""
Parser configurabile per risposte API con strutture JSON annidate.

Progettato per gestire risposte API complesse (es. ItaliaMeteo, GeoJSON)
"""

import re
from typing import Any, Dict, List
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ResponseParser:
    """
    Parser configurabile per risposte API con strutture JSON annidate.

    Trasforma risposte API complesse in una lista piatta di dizionari (righe),
    attraversando ricorsivamente gli array annidati e applicando mappings
    e trasformazioni configurabili.
    """

    def __init__(self, config: Dict = None):
        """
        Inizializza il parser con la configurazione di estrazione.

        Args:
            config: Dizionario con chiavi opzionali:
                - root_path: percorso iniziale nell'albero JSON
                - iterate_on: lista di livelli array da attraversare
                - mappings: regole di estrazione campo → colonna
                - transforms: trasformazioni post-estrazione
        """
        self.config = config or {}
        self.root_path = self.config.get("root_path", "")
        self.iterate_on = self.config.get("iterate_on", [])
        self.mappings = self.config.get("mappings", [])
        self.transforms = self.config.get("transforms", [])

    def parse(self, data: Any) -> List[Dict]:
        """
        Esegue il parsing completo della risposta API.

        Procedura:
            1. Naviga al root_path
            2. Itera ricorsivamente sugli array annidati (iterate_on)
            3. Estrae i campi secondo i mappings
            4. Applica le trasformazioni

        Args:
            data: Risposta JSON dell'API (dict o lista).

        Returns:
            Lista di dizionari (righe), pronti per la conversione in DataFrame.
        """
        # Naviga al punto di partenza nell'albero JSON
        root_data = self._naviga_path(data, self.root_path)

        if root_data is None:
            logger.warning(f"Root path '{self.root_path}' non trovato nella risposta")
            return []

        # Se non ci sono livelli di iterazione, estrazione semplice
        if not self.iterate_on:
            return self._estrazione_semplice(root_data)

        # Estrazione con iterazione ricorsiva su array annidati
        righe = []
        self._itera_ricorsivamente(root_data, livello=0, contesto={}, righe=righe)

        # Applica trasformazioni post-estrazione
        righe = self._applica_trasformazioni(righe)

        logger.info(f"ResponseParser: estratte {len(righe)} righe")
        return righe

    # ITERAZIONE RICORSIVA

    def _itera_ricorsivamente(
        self, data: Any, livello: int, contesto: Dict, righe: List[Dict]
    ):
        """
        Attraversa ricorsivamente gli array annidati definiti in iterate_on.

        Ad ogni livello:
            1. Naviga al path relativo per ottenere l'array
            2. Itera su ogni elemento dell'array
            3. Aggiunge l'elemento al contesto con l'alias configurato
            4. Procede al livello successivo o estrae la riga se è l'ultimo

        Args:
            data: Dati correnti nel livello attuale.
            livello: Indice corrente in iterate_on.
            contesto: Dizionario accumulato {alias: oggetto} per ogni livello.
            righe: Lista risultato (modificata in-place).
        """
        # Se abbiamo attraversato tutti i livelli, estrai la riga
        if livello >= len(self.iterate_on):
            riga = self._estrai_riga(contesto)
            righe.append(riga)
            return

        config_livello = self.iterate_on[livello]
        path = config_livello.get("path", "")
        alias = config_livello.get("as", f"level_{livello}")

        # Naviga al path relativo (o usa i dati correnti se path è vuoto)
        elementi = self._naviga_path(data, path) if path else data

        # Normalizza: garantisci che sia una lista
        if not isinstance(elementi, list):
            elementi = [elementi] if elementi is not None else []

        # Itera sugli elementi del livello corrente
        for elemento in elementi:
            nuovo_contesto = {**contesto, alias: elemento}
            self._itera_ricorsivamente(elemento, livello + 1, nuovo_contesto, righe)

    def _estrai_riga(self, contesto: Dict) -> Dict:
        """
        Estrae una riga applicando i mappings al contesto accumulato.

        Ogni mapping definisce: colonna output, path di estrazione, default.
        Il path usa il formato "alias.campo.subcampo" dove alias
        corrisponde a uno dei livelli in iterate_on.

        Args:
            contesto: Dizionario {alias: oggetto} per ogni livello attraversato.

        Returns:
            Dizionario {nome_colonna: valore} per una riga.
        """
        riga = {}

        for mapping in self.mappings:
            colonna = mapping.get("column", "")
            path = mapping.get("path", "")
            default = mapping.get("default")

            if not colonna or not path:
                continue

            valore = self._estrai_valore_da_contesto(contesto, path)
            riga[colonna] = valore if valore is not None else default

        return riga

    def _estrai_valore_da_contesto(self, contesto: Dict, path: str) -> Any:
        """
        Estrae un valore dal contesto usando un path tipo "station.stat.lat".

        Il primo segmento del path è l'alias del livello in iterate_on.
        I segmenti successivi navigano all'interno dell'oggetto associato.

        Args:
            contesto: Dizionario {alias: oggetto}.
            path: Path nel formato "alias.campo.subcampo".

        Returns:
            Valore estratto, o None se non trovato.
        """
        if not path:
            return None

        parti = path.split(".", 1)
        alias = parti[0]
        path_rimanente = parti[1] if len(parti) > 1 else ""

        if alias not in contesto:
            # Se l'alias non è nel contesto, prova path diretto
            return self._naviga_path(contesto, path)

        oggetto = contesto[alias]

        if path_rimanente:
            return self._naviga_path(oggetto, path_rimanente)
        return oggetto

    # NAVIGAZIONE PATH

    def _naviga_path(self, data: Any, path: str) -> Any:
        """
        Naviga un path nell'albero JSON con supporto per array e filtri.

        Sintassi supportata:
            - Dot notation: "stat.lat" → data["stat"]["lat"]
            - Indice array: "details[0]" → data["details"][0]
            - Indice negativo: "items[-1]" → ultimo elemento
            - Filtro array: "details[var=B01019]" → primo match con var=="B01019"

        Args:
            data: Oggetto JSON di partenza (dict o lista).
            path: Path di navigazione (può essere vuoto).

        Returns:
            Valore raggiunto, o None se il path non è valido.
        """
        if not path or data is None:
            return data

        corrente = data
        segmenti = self._splitta_path(path)

        for segmento in segmenti:
            if corrente is None:
                return None

            # Verifica se il segmento contiene accesso a bracket [...]
            match_bracket = re.match(r"^(\w*)\[(.+)\]$", segmento)

            if match_bracket:
                nome_campo = match_bracket.group(1)
                contenuto_bracket = match_bracket.group(2)

                # Prima naviga al campo (se presente prima del bracket)
                if nome_campo:
                    corrente = self._ottieni_campo(corrente, nome_campo)
                    if corrente is None:
                        return None

                # Poi applica l'accesso bracket (indice o filtro)
                corrente = self._applica_bracket(corrente, contenuto_bracket)
            else:
                # Semplice accesso a campo
                corrente = self._ottieni_campo(corrente, segmento)

        return corrente

    def _splitta_path(self, path: str) -> List[str]:
        """
        Divide un path sui punti, preservando il contenuto delle parentesi quadre.

        Esempio: "stat.details[0].val" → ["stat", "details[0]", "val"]
        """
        segmenti = []
        corrente = ""
        profondita_bracket = 0

        for char in path:
            if char == "[":
                profondita_bracket += 1
                corrente += char
            elif char == "]":
                profondita_bracket -= 1
                corrente += char
            elif char == "." and profondita_bracket == 0:
                if corrente:
                    segmenti.append(corrente)
                corrente = ""
            else:
                corrente += char

        if corrente:
            segmenti.append(corrente)

        return segmenti

    def _ottieni_campo(self, obj: Any, campo: str) -> Any:
        """Accede a un campo di un dizionario o attributo di un oggetto."""
        if isinstance(obj, dict):
            return obj.get(campo)
        elif hasattr(obj, campo):
            return getattr(obj, campo)
        return None

    def _applica_bracket(self, data: Any, contenuto: str) -> Any:
        """
        Applica un accesso bracket a una lista.

        Supporta:
            - Indice numerico positivo: [0], [1], [2]
            - Indice numerico negativo: [-1] (ultimo elemento)
            - Filtro chiave=valore: [var=B01019] (primo match)

        Args:
            data: Lista su cui operare.
            contenuto: Contenuto tra le parentesi quadre.

        Returns:
            Elemento trovato, o None se non valido.
        """
        if not isinstance(data, list):
            return None

        # Indice numerico positivo
        if contenuto.isdigit():
            idx = int(contenuto)
            return data[idx] if 0 <= idx < len(data) else None

        # Indice numerico negativo
        if contenuto.startswith("-") and contenuto[1:].isdigit():
            idx = int(contenuto)
            return data[idx] if abs(idx) <= len(data) else None

        # Filtro chiave=valore (restituisce il primo match)
        if "=" in contenuto:
            chiave, valore = contenuto.split("=", 1)
            for elemento in data:
                if isinstance(elemento, dict) and str(elemento.get(chiave)) == valore:
                    return elemento
            return None

        return None

    # ESTRAZIONE SEMPLICE (senza iterate_on)

    def _estrazione_semplice(self, data: Any) -> List[Dict]:
        """
        Estrazione diretta senza iterazione su array annidati.

        Usata quando iterate_on è vuoto. Applica i mappings direttamente
        su ogni elemento della lista (o sull'unico oggetto).

        Args:
            data: Dati al root_path (lista o singolo oggetto).

        Returns:
            Lista di righe estratte e trasformate.
        """
        elementi = data if isinstance(data, list) else [data]
        righe = []

        for elemento in elementi:
            riga = {}
            for mapping in self.mappings:
                colonna = mapping.get("column", "")
                path = mapping.get("path", "")
                default = mapping.get("default")

                valore = self._naviga_path(elemento, path)
                riga[colonna] = valore if valore is not None else default

            righe.append(riga)

        return self._applica_trasformazioni(righe)

    # TRASFORMAZIONI POST-ESTRAZIONE

    def _applica_trasformazioni(self, righe: List[Dict]) -> List[Dict]:
        """
        Applica le trasformazioni configurate alle righe estratte.

        Ogni trasformazione specifica:
            - column: nome colonna output
            - source: nome colonna sorgente
            - operation: tipo operazione (subtract, add, round, etc.)
            - value: parametro dell'operazione
            - condition: condizione opzionale (es. "variabile == 'B12101'")

        Args:
            righe: Lista di righe da trasformare (modificate in-place).

        Returns:
            Lista di righe trasformate.
        """
        if not self.transforms:
            return righe

        for riga in righe:
            for trasformazione in self.transforms:
                colonna = trasformazione.get("column", "")
                sorgente = trasformazione.get("source", "")
                operazione = trasformazione.get("operation", "")
                valore_op = trasformazione.get("value")
                condizione = trasformazione.get("condition", "")

                if not colonna:
                    continue

                # Valuta condizione opzionale
                if condizione and not self._valuta_condizione(riga, condizione):
                    continue

                valore_sorgente = riga.get(sorgente)

                if valore_sorgente is None:
                    riga[colonna] = None
                    continue

                riga[colonna] = self._applica_operazione(valore_sorgente, operazione, valore_op)

        return righe

    def _applica_operazione(self, valore_sorgente: Any, operazione: str, valore_op: Any) -> Any:
        """
        Esegue un'operazione matematica o di trasformazione stringa.

        Operazioni supportate:
            Matematiche: subtract, add, multiply, divide, round
            Conversioni: kelvin_to_celsius, celsius_to_kelvin
            Stringa: uppercase, lowercase, prefix, suffix, replace
            Logiche: default_if_null

        Args:
            valore_sorgente: Valore su cui operare.
            operazione: Nome dell'operazione.
            valore_op: Parametro dell'operazione (numero, stringa o dict).

        Returns:
            Valore trasformato, o il valore originale in caso di errore.
        """
        try:
            if operazione == "subtract":
                return float(valore_sorgente) - float(valore_op)
            elif operazione == "add":
                return float(valore_sorgente) + float(valore_op)
            elif operazione == "multiply":
                return float(valore_sorgente) * float(valore_op)
            elif operazione == "divide":
                divisore = float(valore_op)
                return float(valore_sorgente) / divisore if divisore != 0 else None
            elif operazione == "round":
                decimali = int(valore_op) if valore_op else 0
                return round(float(valore_sorgente), decimali)
            elif operazione == "kelvin_to_celsius":
                return round(float(valore_sorgente) - 273.15, 2)
            elif operazione == "celsius_to_kelvin":
                return float(valore_sorgente) + 273.15
            elif operazione == "uppercase":
                return str(valore_sorgente).upper()
            elif operazione == "lowercase":
                return str(valore_sorgente).lower()
            elif operazione == "prefix":
                return f"{valore_op}{valore_sorgente}"
            elif operazione == "suffix":
                return f"{valore_sorgente}{valore_op}"
            elif operazione == "replace":
                # valore_op è un dict {"find": "x", "replace": "y"}
                if isinstance(valore_op, dict):
                    cerca = valore_op.get("find", "")
                    sostituisci = valore_op.get("replace", "")
                    return str(valore_sorgente).replace(cerca, sostituisci)
            elif operazione == "default_if_null":
                return valore_sorgente if valore_sorgente is not None else valore_op
            else:
                return valore_sorgente
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.warning(f"Errore trasformazione '{operazione}': {e}")
            return valore_sorgente

    def _valuta_condizione(self, riga: Dict, condizione: str) -> bool:
        """
        Valuta una condizione semplice su una riga.

        Supporta operatori == e != con sintassi: "colonna == 'valore'"

        Args:
            riga: Dizionario della riga corrente.
            condizione: Stringa condizione (es. "variabile == 'B12101'").

        Returns:
            True se la condizione è soddisfatta (o se non parsabile).
        """
        try:
            match = re.match(r"(\w+)\s*(==|!=)\s*['\"]?([^'\"]+)['\"]?", condizione)
            if not match:
                return True

            colonna, operatore, valore_atteso = match.groups()
            valore_riga = str(riga.get(colonna, ""))

            if operatore == "==":
                return valore_riga == valore_atteso
            elif operatore == "!=":
                return valore_riga != valore_atteso

            return True
        except Exception as e:
            logger.debug("_apply_filter_condition: filtro fallito, riga inclusa per default: %s", e)
            return True


# FUNZIONE HELPER

def parse_response(data: Any, config: Dict) -> List[Dict]:
    """
    Helper per parsing rapido di una risposta API.

    Crea un ResponseParser con la configurazione fornita e
    esegue immediatamente il parsing.

    Args:
        data: Risposta JSON dell'API.
        config: Configurazione del parser (root_path, iterate_on, mappings, transforms).

    Returns:
        Lista di dizionari (righe) estratte dalla risposta.
    """
    parser = ResponseParser(config)
    return parser.parse(data)


# CONFIGURAZIONI PRESET PER FORMATI COMUNI

PRESET_CONFIGS = {
    # ItaliaMeteo: risposta con stazioni → prodotti → misurazioni
    "italiameteo_observations": {
        "root_path": "data",
        "iterate_on": [
            {"path": "", "as": "station"},
            {"path": "prod", "as": "product"},
            {"path": "val", "as": "measurement"}
        ],
        "mappings": [
            {"column": "lat", "path": "station.stat.lat"},
            {"column": "lon", "path": "station.stat.lon"},
            {"column": "rete", "path": "station.stat.net"},
            {"column": "nome_stazione", "path": "station.stat.details[0].val"},
            {"column": "variabile", "path": "product.var"},
            {"column": "valore", "path": "measurement.val"},
            {"column": "timestamp", "path": "measurement.ref"}
        ],
        "transforms": [
            {
                "column": "valore_celsius",
                "source": "valore",
                "operation": "kelvin_to_celsius",
                "condition": "variabile == 'B12101'"
            }
        ]
    },

    # GeoJSON FeatureCollection: estrae geometrie e proprietà
    "geojson_features": {
        "root_path": "features",
        "iterate_on": [
            {"path": "", "as": "feature"}
        ],
        "mappings": [
            {"column": "geometry", "path": "feature.geometry"},
            {"column": "id", "path": "feature.id"}
            # I mappings per le properties vengono aggiunti dinamicamente
        ]
    },

    # Array semplice: itera su un array al root level
    "simple_array": {
        "root_path": "",
        "iterate_on": [
            {"path": "", "as": "item"}
        ],
        "mappings": []  # Da configurare per il caso specifico
    }
}


def get_preset_config(preset_name: str) -> Dict:
    """
    Restituisce una copia della configurazione preset per un formato comune.

    Preset disponibili: italiameteo_observations, geojson_features, simple_array.

    Args:
        preset_name: Nome del preset.

    Returns:
        Copia della configurazione preset, o dict vuoto se non trovato.
    """
    return PRESET_CONFIGS.get(preset_name, {}).copy()
