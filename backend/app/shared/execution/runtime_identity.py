"""Input «Utente che esegue»: il valore lo inietta SOLO il server.

REGOLA (unica, per tutti i percorsi)
------------------------------------
Il valore di un input di tipo server (``runtime_input_kinds``, oggi
``current_user``) e' lo USERNAME DI LOGIN dell'utente autenticato che avvia,
letto dal DB al momento della richiesta (``auth.dependencies.get_current_user``).
Lo risolve una sola funzione, :func:`resolve_identity_inputs`, chiamata da
``eseguibili.runtime.resolve_runtime_inputs`` (avvio e stima degli Eseguibili,
esecuzione/stima/Valida del progetto) e dall'impronta degli Eseguibili.

- Valore inviato dal client per quell'input -> rifiutato
  (``runtime_identity_client_value``), mai usato.
- Chi chiama dichiara l'identita' con :class:`RuntimeIdentity`:

  ============================  ==============================================
  percorso                      identita'
  ============================  ==============================================
  Eseguibile: stima e avvio     ``authenticated(current_user)``
  progetto: «Esegui», stima     ``project_run()`` -> SI BLOCCA (nessun utente:
  di Arricchisci, enrich-async  dall'editor non si salva un risultato filtrato
                                con un utente di prova)
  progetto: «Esegui di prova»   ``test_run(canvas SALVATO)``: lo username di
  (``/test-run``: stima e       PROVA del nodo; vuoto -> si blocca. Solo chi
  avvio)                        puo' modificare il progetto; risultato
                                ISOLATO (sezione «Esecuzione di prova»).
  anteprima, Valida con input,  ``preview_of(canvas)``: lo username di PROVA
  stima del nodo API, valori    scritto nel nodo (``preview_username``); vuoto
  distinti                      -> si blocca. Mai per un'esecuzione che salva
                                risultati.
  Schedule, catene, Carica,     nessuna: il canvas arriva agli executor con il
  worker                        nodo ancora presente e
                                :func:`assert_identity_resolved` blocca.
  ============================  ==============================================

Senza utente la catena si blocca con ``runtime_identity_required`` e un
messaggio che dice cosa fare.

ESECUZIONE DI PROVA (regola H, 05/11/2026)
------------------------------------------
Un progetto con «Utente che esegue» si prova dall'editor SOLO cosi':

- chi: chi puo' modificare il progetto (``require_user_or_above`` + gruppo
  del progetto, come il salvataggio del canvas); esecutore e viewer -> 403;
- con chi: lo username di prova del nodo (``preview_username``) letto dal
  canvas SALVATO sul server, mai dal client e mai l'utente che lancia
  (nessun ripiego silenzioso su un'altra identita'); senza -> si blocca;
- dove finisce: il contratto porta ``execution_subject`` ``test_run`` e il
  risultato va nello spazio dell'attivita' (come un Eseguibile: oggetto per
  ``task_id``, scaricabile una volta solo da chi l'ha lanciata), MAI nei
  risultati del progetto: nessun ``output_contract`` aggiornato, nessuna riga
  ``results`` (nessuna sorgente «progetto» a valle cambia), nessuna run
  intermedia (cronologia dei nodi e retention invariate), nessun Carica;
- cronologia: la riga di ``log_runs`` ha ``triggered_by`` =
  ``test_run:<username>`` (:func:`trigger_for_test_run`), mostrata come
  «Esecuzione di prova (utente: X)» ed esclusa dallo stato «ultima
  esecuzione» del progetto. Il prefisso ``test_run:`` lo scrive SOLO il
  server (questa factory): ``triggered_by`` dal client e' ammesso solo da
  :data:`CLIENT_TRIGGERS` e un Carica non e' mai una prova
  (:func:`is_test_run_trigger`);
- Schedule e catene non passano mai di qui: restano bloccati.

REGOLA MADRE (correzione dopo la conferma di G+H): la prova non scrive
NULLA fuori dallo spazio della propria attivita' e dalla riga ``log_runs``
marcata. Di conseguenza:

- nessun ricalcolo a monte: ``force_recalculate`` non e' ammesso (gli
  endpoint di prova lo rifiutano, :data:`TEST_RUN_NO_RECALCULATE_MESSAGE`; il
  worker lo ignora) e se l'artefatto del progetto a monte manca o e' scaduto
  la prova si ferma con «Esegui prima il progetto a monte X»
  (:func:`guard_upstream_recompute`, l'unica porta del ricalcolo a monte di
  Manipola e Arricchisci, che conosce il soggetto dell'esecuzione);
- nessuna risorsa condivisa aggiornata (es. lo schema della connessione API
  rilevato da Arricchisci);
- il ruolo si ricontrolla anche al prelievo dal worker e al download del
  risultato (:func:`can_test_run`, la stessa regola di
  ``require_user_or_above``): un utente retrocesso mentre la prova e' in coda
  la vede annullata con il motivo e non scarica il risultato;
- ogni esito del worker (anche connessione persa e permesso revocato) porta
  il segno della prova.

Due usi dello stesso valore (``runtime_input_kinds.project_input_role``):
parametro (binding ``node_field``: filtro di Estrai, parametro di un passo,
parametro API) oppure, in Manipola, SORGENTE (``as_source``): il nodo
diventa un nodo Valori statici di una riga con la colonna ``source_column``
(:func:`identity_source_fields`, stessa forma per inferenza ed esecuzione).

TRASFORMAZIONE (09/11/2026)
---------------------------
L'input puo' dichiarare una «Trasformazione» (``identity_transform`` nel nodo,
``transform`` nella definizione del manifest pubblicato): un'espressione con
la sintassi e il cancello AST della Formula di Manipola, variabile
:data:`TRANSFORM_VARIABLE` (``utente``). Regola unica: il VALORE dell'input e'
il risultato della trasformazione sul nome utente dell'identita' (senza
trasformazione, il nome utente com'e'). Lo calcola SOLO
:func:`resolve_identity_inputs`, una volta per esecuzione, con il valutatore
del motore della Formula passato da chi chiama (``evaluate``, obbligatorio:
questo pacchetto non importa ``app.core``); vale per ogni uso, parametro e
sorgente, e per ogni identita' (utente autenticato, nome di prova).

- Il client non imposta ne' il valore ne' la trasformazione: questa sta nel
  manifest pubblicato (o nel canvas del progetto, per l'editor).
- La valutazione fallisce, oppure non da' un testo non vuoto (``None``,
  numero, vero/falso, testo vuoto) -> l'esecuzione si ferma con
  ``runtime_identity_transform_failed`` e un messaggio che lo dice: mai un
  valore inventato. Diverso dal caso «trasformato ma senza righe» (per
  esempio ``dmi_IS`` per ``admin``): il valore e' valido, il filtro non trova
  righe, il file e' vuoto.

VALORE SCELTO DAGLI AMMINISTRATORI (09/11/2026)
-----------------------------------------------
L'input puo' dichiarare «Gli amministratori possono scegliere il valore»
(``admin_override: true`` nel nodo e nella definizione del manifest, con
l'etichetta del campo ``admin_override_label``). Regola unica, in
:func:`resolve_identity_inputs` (:meth:`RuntimeIdentity.may_choose`):

- chi esegue e' un utente autenticato (Eseguibile: avvio e stima) con ruolo
  ``admin`` o ``superadmin`` (:data:`ADMIN_OVERRIDE_ROLES`, letto dal server
  in ``RuntimeIdentity.authenticated(current_user)``, mai dal client) E
  l'input ha ``admin_override`` -> puo' inviare un valore: non vuoto -> si
  usa cosi' com'e' (NESSUNA trasformazione: e' gia' il valore finale), come
  testo (:func:`chosen_value`: caratteri di controllo e spazi ai lati tolti,
  al massimo :data:`ADMIN_OVERRIDE_MAX_LENGTH` caratteri, altrimenti
  ``runtime_identity_override_invalid``); vuoto o assente -> regola normale
  (nome utente + trasformazione);
- ogni altro ruolo (esecutore, user, viewer), ogni altra identita' (prova,
  anteprima, nessun utente) o input senza ``admin_override`` -> un valore
  del client si rifiuta (``runtime_identity_client_value``), come sempre;
- il valore scelto segue la stessa via del valore del server (segno
  ``value_literal``: in Estrai solo «uguale» o «e' uno di», un solo
  parametro SQL, mai una funzione SQL);
- audit: il riepilogo della run porta ``overridden_by`` (lo username di chi
  l'ha scelto) e ``value`` (:func:`identity_audit`).

Nell'editor (anteprima, Valida, «Esegui di prova») chi modifica il progetto
puo' scrivere nell'input un «valore di prova scelto»
(:data:`PREVIEW_ADMIN_VALUE_FIELD`, letto solo con ``admin_override``; per
la prova dal canvas SALVATO): si esegue con quel valore invece del nome
utente di prova trasformato. E' lo stesso potere che chi modifica ha gia'
con nome di prova e trasformazione; il nome utente di prova resta
obbligatorio (e' chi «esegue» la prova). Non entra mai nel manifest; il
riepilogo lo segna con ``test_value``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
import unicodedata

from app.shared import error_codes
from app.shared.contracts.auth import (
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    CurrentUser,
    username_form_error,
)
from app.shared.execution.runtime_input_kinds import (
    SOURCE_COLUMN_FIELD,
    is_server_injected,
    node_type,
    node_value,
)

IdentityOrigin = Literal["authenticated", "preview", "test_run", "absent"]
AbsentReason = Literal[
    "project_run", "unattended", "preview_missing", "preview_conflict", "username_invalid", "test_run_missing",
]

PREVIEW_USERNAME_FIELD = "preview_username"
KIND_LABEL = "Utente che esegue"
# Esecuzione di prova dall'editor (regola H): tipo del soggetto del contratto
# e prefisso di ``log_runs.triggered_by``.
TEST_RUN_SUBJECT_KIND = "test_run"
TEST_RUN_TRIGGER_PREFIX = "test_run:"
# Sorgente «Utente che esegue» (Manipola): una riga, una colonna di testo.
DEFAULT_SOURCE_COLUMN = "utente"
# Trasformazione dell'input (sezione TRASFORMAZIONE): campo del nodo e nome
# della variabile dell'espressione.
TRANSFORM_FIELD = "identity_transform"
TRANSFORM_VARIABLE = "utente"
TRANSFORM_MAX_LENGTH = 500
# Valore scelto dagli amministratori (sezione VALORE SCELTO DAGLI
# AMMINISTRATORI): campi del nodo e della definizione, ruoli, limite.
ADMIN_OVERRIDE_FIELD = "admin_override"
ADMIN_OVERRIDE_LABEL_FIELD = "admin_override_label"
PREVIEW_ADMIN_VALUE_FIELD = "preview_admin_value"
ADMIN_OVERRIDE_ROLES: tuple[str, ...] = ROLES_ADMIN_OR_ABOVE
ADMIN_OVERRIDE_MAX_LENGTH = 200
# Valutatore del motore della Formula: (espressione, {variabile: valore}) ->
# valore della riga. Lo passa chi chiama (app.core).
IdentityEvaluator = Callable[[str, Mapping[str, str]], Any]

_MISSING_MESSAGES: dict[str, str] = {
    "project_run": (
        "L'input «{label}» ({kind}) ha un valore solo quando la catena e' avviata "
        "come Eseguibile da un utente autenticato: «Esegui» del progetto non ha "
        "un utente. Pubblicalo come Eseguibile, oppure usa «Esegui di prova» "
        "con un nome utente di prova nell'input."
    ),
    "test_run_missing": (
        "L'input «{label}» ({kind}): per l'esecuzione di prova indica un nome "
        "utente di prova nell'input e salva il progetto."
    ),
    "unattended": (
        "L'input «{label}» ({kind}) non ha un utente: la catena e' partita senza "
        "un utente autenticato (Pianificazione, catena o avvio non presidiato) e si "
        "blocca. Avviala come Eseguibile."
    ),
    "preview_missing": (
        "L'input «{label}» ({kind}): per anteprima e Valida indica un nome "
        "utente di prova nell'input."
    ),
    "username_invalid": (
        "L'input «{label}» ({kind}): lo username dell'utente che avvia ha spazi "
        "iniziali o finali o caratteri di controllo e non si usa come filtro. "
        "Chiedi all'amministratore di correggerlo."
    ),
    "preview_conflict": (
        "Gli input «{kind}» hanno nomi utente di prova diversi: usane uno solo "
        "(input «{label}»)."
    ),
}


class RuntimeIdentityError(ValueError):
    """Blocco tipizzato dell'input «Utente che esegue» (``code`` del catalogo)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RuntimeIdentity:
    """Chi esegue, come lo vede il server. Si costruisce solo dalle factory."""

    origin: IdentityOrigin
    username: str | None = None
    reason: AbsentReason = "unattended"
    # Ruolo di chi esegue: solo per ``authenticated``, letto dal server.
    role: str | None = None
    # Valori di prova scelti nei nodi (anteprima e prova): ((chiave, valore), ...).
    test_values: tuple[tuple[str, str], ...] = ()

    # REGOLA (correzione dopo la conferma, punto 3): le factory non
    # sollevano MAI. Ogni blocco (utente assente, nomi di prova diversi) nasce
    # in :func:`resolve_identity_inputs`, cioe' dentro la preparazione di ogni
    # percorso e il suo mappatore unico degli errori (400 tipizzato).

    @classmethod
    def authenticated(cls, user: CurrentUser) -> "RuntimeIdentity":
        """Lo username ESATTO del DB (punto 2): nessuno strip, nessuna
        trasformazione. Gli username nuovi sono gia' senza spazi iniziali e
        finali e unici in forma normalizzata (``auth.usernames``); uno
        username vuoto o di soli spazi non e' un utente."""
        username = getattr(user, "username", None)
        if not isinstance(username, str) or not username.strip():
            return cls.unattended()
        if username_form_error(username) is not None:
            # Utente creato prima della regola: mai uno username che un
            # database potrebbe confrontare uguale a quello di un altro.
            return cls("absent", None, "username_invalid")
        role = getattr(user, "role", None)
        return cls("authenticated", username, role=role if isinstance(role, str) else None)

    def may_choose(self, item: Any) -> bool:
        """Chi esegue puo' inviare il valore di ``item`` (sezione VALORE
        SCELTO DAGLI AMMINISTRATORI): utente autenticato admin/superadmin e
        input con ``admin_override``."""
        return (
            self.origin == "authenticated"
            and self.role in ADMIN_OVERRIDE_ROLES
            and admin_override_enabled(item)
        )

    def test_value(self, key: str) -> Any:
        """Valore di prova scelto nel nodo ``key`` (anteprima e prova)."""
        return dict(self.test_values).get(key)

    @classmethod
    def project_run(cls) -> "RuntimeIdentity":
        return cls("absent", None, "project_run")

    @classmethod
    def unattended(cls) -> "RuntimeIdentity":
        return cls("absent", None, "unattended")

    @classmethod
    def preview_of(cls, canvas: Any) -> "RuntimeIdentity":
        """Username di prova scritto nei nodi «Utente che esegue» del canvas."""
        return cls._test_username(canvas, origin="preview", missing="preview_missing")

    @classmethod
    def test_run(cls, saved_canvas: Any) -> "RuntimeIdentity":
        """«Esegui di prova» dall'editor (regola H): lo username di prova del
        canvas SALVATO (chi chiama passa quello letto dal server). Stessa
        regola di :meth:`preview_of`, origine distinta: contratto e impronta
        sanno che e' una prova."""
        return cls._test_username(saved_canvas, origin="test_run", missing="test_run_missing")

    @classmethod
    def _test_username(cls, canvas: Any, *, origin: IdentityOrigin, missing: AbsentReason) -> "RuntimeIdentity":
        names = {
            str(node_value(node, PREVIEW_USERNAME_FIELD) or "").strip()
            for node in identity_input_nodes(canvas)
        }
        names.discard("")
        if len(names) > 1:
            return cls("absent", None, "preview_conflict")
        if not names:
            return cls("absent", None, missing)
        chosen = tuple(
            (str(node_value(node, "input_key") or ""), str(node_value(node, PREVIEW_ADMIN_VALUE_FIELD)))
            for node in identity_input_nodes(canvas)
            if admin_override_enabled(node) and node_value(node, PREVIEW_ADMIN_VALUE_FIELD) is not None
        )
        return cls(origin, names.pop(), test_values=chosen)


def _canvas_nodes(canvas: Any) -> list[Any]:
    if isinstance(canvas, Mapping):
        nodes = canvas.get("nodes")
    else:
        nodes = getattr(canvas, "nodes", None)
    return list(nodes or [])


def identity_input_nodes(canvas: Any) -> list[Any]:
    """Nodi «Input di progetto» di tipo server (il valore lo mette il server)."""
    return [
        node
        for node in _canvas_nodes(canvas)
        if node_type(node) == "project_input"
        and is_server_injected(node_value(node, "input_kind"))
    ]


def _label(item: Any) -> str:
    return str(
        node_value(item, "label") or node_value(item, "key")
        or node_value(item, "input_key") or KIND_LABEL
    )


def identity_source_column(node: Any) -> str:
    """Nome della colonna della sorgente «Utente che esegue» (default ``utente``)."""
    return str(node_value(node, SOURCE_COLUMN_FIELD) or "").strip() or DEFAULT_SOURCE_COLUMN


def identity_source_fields(column: str, username: str) -> list[dict[str, str]]:
    """La sorgente «Utente che esegue» E' un nodo Valori statici con un campo
    di testo: la stessa forma per l'inferenza dello schema (valore di prova
    qualunque, il tipo e' sempre testo) e per l'esecuzione (lo username).
    """
    if "{{" in username:
        # I Valori statici risolvono le variabili {{...}}: uno username che
        # le contiene cambierebbe valore. Mai un filtro su un valore diverso.
        raise RuntimeIdentityError(
            error_codes.RUNTIME_IDENTITY_REQUIRED,
            f"Il nome utente contiene '{{{{': non si puo' usare come sorgente «{KIND_LABEL}».",
        )
    return [{"name": column, "value": username, "field_type": "string"}]


def missing_identity_error(label: str, reason: str) -> RuntimeIdentityError:
    message = _MISSING_MESSAGES.get(reason, _MISSING_MESSAGES["unattended"])
    return RuntimeIdentityError(
        error_codes.RUNTIME_IDENTITY_REQUIRED,
        message.format(label=label, kind=KIND_LABEL),
    )


def identity_transform(item: Any) -> str | None:
    """Trasformazione dichiarata da un nodo (``identity_transform``) o da una
    definizione del manifest (``transform``); ``None`` se assente o vuota."""
    raw = node_value(item, TRANSFORM_FIELD)
    if raw is None and not isinstance(item, Mapping) and node_type(item) is None:
        # Definizione del manifest (nessun tipo di nodo): campo ``transform``.
        raw = getattr(item, "transform", None)
    text = str(raw or "").strip()
    return text or None


def _transform_error(label: str, username: str, expression: str, reason: str) -> RuntimeIdentityError:
    return RuntimeIdentityError(
        error_codes.RUNTIME_IDENTITY_TRANSFORM_FAILED,
        f"Il nome utente «{username}» non si trasforma con la regola dell'input "
        f"«{label}» ({KIND_LABEL}: `{expression}`): {reason}. L'esecuzione si ferma "
        "(nessun valore sostitutivo). Correggi la trasformazione o il nome utente.",
    )


def transform_identity_value(
    *, label: str, username: str, expression: str | None, evaluate: IdentityEvaluator,
) -> str:
    """Valore dell'input per ``username`` (sezione TRASFORMAZIONE): la
    trasformazione valutata dal motore della Formula, oppure lo username."""
    if not expression:
        return username
    try:
        value = evaluate(expression, {TRANSFORM_VARIABLE: username})
    except (ValueError, TypeError, ArithmeticError, AttributeError, KeyError, IndexError) as exc:
        raise _transform_error(label, username, expression, "la regola non si applica") from exc
    if not isinstance(value, str):
        what = "nessun valore" if value is None else f"un valore che non e' un testo ({value!r})"
        raise _transform_error(label, username, expression, f"la regola da' {what}")
    if not value:
        raise _transform_error(label, username, expression, "la regola da' un testo vuoto")
    return str(value)


def admin_override_enabled(item: Any) -> bool:
    """L'input (nodo o definizione del manifest) dichiara «Gli amministratori
    possono scegliere il valore»: solo il valore ``True``."""
    return node_value(item, ADMIN_OVERRIDE_FIELD) is True


def admin_override_label(item: Any) -> str | None:
    """Etichetta del campo per gli amministratori, ``None`` se assente."""
    text = str(node_value(item, ADMIN_OVERRIDE_LABEL_FIELD) or "").strip()
    return text or None


def chosen_value(label: str, raw: Any) -> str | None:
    """Valore scelto (da un amministratore o di prova) come testo: caratteri
    di controllo e spazi ai lati tolti; ``None`` se vuoto (vale la regola
    normale). Non testo o troppo lungo -> ``runtime_identity_override_invalid``."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RuntimeIdentityError(
            error_codes.RUNTIME_IDENTITY_OVERRIDE_INVALID,
            f"Il valore scelto per l'input «{label}» ({KIND_LABEL}) deve essere un testo.",
        )
    text = "".join(ch for ch in raw if unicodedata.category(ch) != "Cc").strip()
    if not text:
        return None
    if len(text) > ADMIN_OVERRIDE_MAX_LENGTH:
        raise RuntimeIdentityError(
            error_codes.RUNTIME_IDENTITY_OVERRIDE_INVALID,
            f"Il valore scelto per l'input «{label}» ({KIND_LABEL}) supera "
            f"{ADMIN_OVERRIDE_MAX_LENGTH} caratteri.",
        )
    return text


class IdentityValues(dict):
    """``{chiave: valore}`` degli input di tipo server, con le chiavi il cui
    valore e' stato scelto (``overridden``: da un amministratore o di prova)."""

    overridden: frozenset[str] = frozenset()


def resolve_identity_inputs(
    definitions: Iterable[Any],
    *,
    identity: RuntimeIdentity,
    client_values: Mapping[str, Any],
    evaluate: IdentityEvaluator,
) -> IdentityValues:
    """Valori degli input di tipo server: ``{chiave: valore}``.

    ``definitions`` sono le definizioni del manifest (``key``, ``label``,
    ``kind``, ``transform``, ``admin_override``). Un valore del client per
    una di queste chiavi si rifiuta, salvo :meth:`RuntimeIdentity.may_choose`
    (sezione VALORE SCELTO DAGLI AMMINISTRATORI); senza utente si blocca con
    il motivo del percorso; un valore scelto non vuoto si usa com'e'; con
    una trasformazione il valore e' il suo risultato (``evaluate``, sezione
    TRASFORMAZIONE), senza e' lo username.
    """
    targets = [item for item in definitions if is_server_injected(getattr(item, "kind", None))]
    for item in targets:
        if item.key in client_values and not identity.may_choose(item):
            raise RuntimeIdentityError(
                error_codes.RUNTIME_IDENTITY_CLIENT_VALUE,
                f"Il valore dell'input «{item.label}» ({KIND_LABEL}) lo imposta il "
                "server con l'utente che avvia: non si invia.",
            )
    if not targets:
        return IdentityValues()
    if identity.username is None:
        raise missing_identity_error(targets[0].label, identity.reason)
    chosen: dict[str, str] = {}
    for item in targets:
        if identity.may_choose(item):
            raw = client_values.get(item.key)
        elif identity.origin in ("preview", "test_run") and admin_override_enabled(item):
            raw = identity.test_value(item.key)
        else:
            continue
        value = chosen_value(str(item.label), raw)
        if value is not None:
            chosen[item.key] = value
    values = IdentityValues({
        item.key: chosen[item.key] if item.key in chosen else transform_identity_value(
            label=str(item.label), username=identity.username,
            expression=identity_transform(item), evaluate=evaluate,
        )
        for item in targets
    })
    values.overridden = frozenset(chosen)
    return values


def identity_audit(identity: RuntimeIdentity, values: Mapping[str, str], key: str) -> dict[str, Any]:
    """Campi dell'audit (riepilogo della run) per un valore scelto: chi l'ha
    scelto (amministratore) oppure il segno del valore di prova."""
    if key not in getattr(values, "overridden", frozenset()):
        return {}
    if identity.origin == "authenticated":
        return {"overridden_by": identity.username, "value": values[key]}
    return {"test_value": True, "value": values[key]}


def subject_for_test_run(identity: RuntimeIdentity) -> dict[str, str]:
    """Soggetto ``test_run`` del contratto di un'esecuzione di prova."""
    if identity.origin != "test_run" or not identity.username:
        raise ValueError("Soggetto di prova senza un'identita' di prova")
    return {"kind": TEST_RUN_SUBJECT_KIND, "username": identity.username}


def trigger_for_test_run(username: str) -> str:
    """``log_runs.triggered_by`` di un'esecuzione di prova."""
    return f"{TEST_RUN_TRIGGER_PREFIX}{username}"


TEST_RUN_RESULT_FIELD = "test_run"


def task_result(payload: Any, test_run: Any) -> dict[str, Any]:
    """L'UNICA funzione che costruisce il risultato di un task (Pipeline e
    Arricchisci, worker e finalizzazione dell'esito): con il soggetto di prova
    ``test_run`` (``{"username": ...}``) il risultato porta il segno
    ``test_run`` in OGNI esito (successo, fallimento, permesso revocato,
    idempotenza, connessione persa, eccezione, annullamento); senza, e' il
    payload invariato."""
    result = dict(payload or {})
    if isinstance(test_run, Mapping):
        result[TEST_RUN_RESULT_FIELD] = {"username": str(test_run.get("username") or "")}
    return result


def contract_test_run_subject(execution_contract: Any) -> dict[str, str] | None:
    """Il soggetto di prova dal contratto grezzo di un messaggio del worker
    (``execution_contract.execution_subject``), ``None`` se non e' una prova
    o il contratto non e' leggibile: serve a chi scrive l'esito di un task
    finito in eccezione, senza il risultato del task."""
    if not isinstance(execution_contract, Mapping):
        return None
    subject = execution_contract.get("execution_subject")
    if not isinstance(subject, Mapping) or subject.get("kind") != TEST_RUN_SUBJECT_KIND:
        return None
    username = subject.get("username")
    return {"username": username} if isinstance(username, str) and username.strip() else None


# Valori di ``triggered_by`` che un client puo' dichiarare (POST /tasks).
# Tutti gli altri li scrive solo il server (scheduler, cascata, pipeline,
# Eseguibili, prova).
CLIENT_TRIGGERS: tuple[str, ...] = ("manual",)

TEST_RUN_NO_RECALCULATE_MESSAGE = (
    "L'esecuzione di prova non ricalcola nulla: usa il risultato salvato dei "
    "progetti a monte. Togli «Ricalcola tutto»; se un progetto a monte non e' "
    "aggiornato, eseguilo prima."
)
TEST_RUN_ROLE_REVOKED_MESSAGE = (
    "Esecuzione di prova annullata: il tuo ruolo non permette piu' di "
    "modificare il progetto (servono i permessi di chi lo modifica)."
)


def is_test_run_trigger(value: Any) -> bool:
    """``triggered_by`` di un'esecuzione di prova (prefisso ``test_run:``)."""
    return isinstance(value, str) and value.startswith(TEST_RUN_TRIGGER_PREFIX)


def client_trigger(value: Any) -> str:
    """``triggered_by`` dichiarato da un client: solo da :data:`CLIENT_TRIGGERS`."""
    if value not in CLIENT_TRIGGERS:
        raise ValueError(
            "triggered_by non ammesso: i valori dal client sono "
            + ", ".join(CLIENT_TRIGGERS)
        )
    return str(value)


def can_test_run(role: Any) -> bool:
    """Chi puo' lanciare (e tenere) un'esecuzione di prova: chi puo'
    modificare il progetto, cioe' ``require_user_or_above`` (il gruppo lo
    verifica chi chiama). Stessa regola all'avvio, al prelievo dal worker e
    al download del risultato."""
    return role in ROLES_ABOVE_VIEWER


class TestRunUpstreamRefused(ValueError):
    """La prova avrebbe ricalcolato un progetto a monte: si ferma."""

    __test__ = False  # non e' una classe di test (pytest)

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        super().__init__(
            f"Esecuzione di prova: il progetto a monte «{source_name}» non ha un "
            "risultato aggiornato e la prova non lo ricalcola (non scrive nulla "
            f"fuori da se'). Esegui prima il progetto a monte «{source_name}», poi "
            "rilancia la prova."
        )


def guard_upstream_recompute(*, test_run: bool, source_name: str) -> None:
    """L'UNICA porta del ricalcolo di un progetto a monte (Manipola e
    Arricchisci, sorgente «progetto»): chiamata prima di qualunque scrittura
    del ricalcolo (run intermedia, ``results``, ``output_contract``,
    ``log_runs``, Carica). In un'esecuzione di prova solleva
    :class:`TestRunUpstreamRefused`; altrimenti non fa nulla."""
    if test_run:
        raise TestRunUpstreamRefused(source_name)


def assert_identity_resolved(canvas: Any) -> None:
    """Ultima difesa agli executor: un nodo «Utente che esegue» ancora nel
    canvas vuol dire che nessuno ha risolto l'utente (Schedule, catene,
    Carica, worker) -> la catena si blocca."""
    nodes = identity_input_nodes(canvas)
    if nodes:
        raise missing_identity_error(_label(nodes[0]), RuntimeIdentity.unattended().reason)


__all__ = [
    "ADMIN_OVERRIDE_FIELD",
    "ADMIN_OVERRIDE_LABEL_FIELD",
    "ADMIN_OVERRIDE_MAX_LENGTH",
    "ADMIN_OVERRIDE_ROLES",
    "IdentityValues",
    "PREVIEW_ADMIN_VALUE_FIELD",
    "admin_override_enabled",
    "admin_override_label",
    "chosen_value",
    "identity_audit",
    "CLIENT_TRIGGERS",
    "DEFAULT_SOURCE_COLUMN",
    "TEST_RUN_NO_RECALCULATE_MESSAGE",
    "TEST_RUN_ROLE_REVOKED_MESSAGE",
    "TestRunUpstreamRefused",
    "can_test_run",
    "client_trigger",
    "guard_upstream_recompute",
    "is_test_run_trigger",
    "KIND_LABEL",
    "PREVIEW_USERNAME_FIELD",
    "TEST_RUN_SUBJECT_KIND",
    "TEST_RUN_TRIGGER_PREFIX",
    "RuntimeIdentity",
    "RuntimeIdentityError",
    "assert_identity_resolved",
    "identity_input_nodes",
    "identity_source_column",
    "identity_source_fields",
    "identity_transform",
    "IdentityEvaluator",
    "TRANSFORM_FIELD",
    "TRANSFORM_MAX_LENGTH",
    "TRANSFORM_VARIABLE",
    "transform_identity_value",
    "missing_identity_error",
    "resolve_identity_inputs",
    "subject_for_test_run",
    "trigger_for_test_run",
    "TEST_RUN_RESULT_FIELD",
    "contract_test_run_subject",
    "task_result",
]
