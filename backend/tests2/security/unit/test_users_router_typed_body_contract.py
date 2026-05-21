"""
Contract test per `UserUpdateRequest` (PUT /auth/users/{user_id}).

Bug coperto (plenora-bug-security-auth P1):
  Pre-fix l'endpoint accettava `data: dict` come body parameter,
  bypassando Pydantic validation a livello router. Il codice applicava
  whitelist + type check manuali, fragili a regressioni di refactor.
  Lo schema OpenAPI mostrava un body opaque senza guidance per i client.

Post-fix: `UserUpdateRequest` Pydantic con `extra="forbid"` rifiuta
campi sconosciuti, field validator per email/role/group_id, model
validator per messaggio user-facing su `password` errato endpoint.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.auth.models import UserUpdateRequest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


class TestUserUpdateRequestExtraForbid:
    """`extra="forbid"` deve rifiutare ogni campo non in schema. Pre-fix
    il dict raw accettava qualsiasi chiave e la scartava silenziosamente,
    facendo credere all'utente che il campo fosse stato applicato.
    """

    def test_password_rejected_with_friendly_message(self):
        with pytest.raises(ValidationError) as exc_info:
            UserUpdateRequest.model_validate({"password": "newpw123"})
        assert "POST /users/{id}/reset-password" in str(exc_info.value)

    def test_arbitrary_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            UserUpdateRequest.model_validate({"admin_override": True})


class TestUserUpdateRequestFieldValidation:
    """Type / format check ora schema-first."""

    def test_email_must_be_valid_format(self):
        with pytest.raises(ValidationError, match="Formato email"):
            UserUpdateRequest.model_validate({"email": "no-at-sign"})

    def test_email_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="non vuota"):
            UserUpdateRequest.model_validate({"email": ""})

    def test_email_valid_accepted(self):
        m = UserUpdateRequest.model_validate({"email": "user@example.com"})
        assert m.email == "user@example.com"

    def test_role_must_be_in_valid_roles(self):
        with pytest.raises(ValidationError, match="Ruolo non valido"):
            UserUpdateRequest.model_validate({"role": "god_mode"})

    @pytest.mark.parametrize("valid_role", ["user", "admin", "superadmin"])
    def test_role_valid_accepted(self, valid_role):
        m = UserUpdateRequest.model_validate({"role": valid_role})
        assert m.role == valid_role

    def test_is_active_must_be_bool(self):
        with pytest.raises(ValidationError):
            UserUpdateRequest.model_validate({"is_active": "yes"})

    def test_group_id_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="non vuota"):
            UserUpdateRequest.model_validate({"group_id": ""})


class TestUserUpdateRequestPartialUpdate:
    """exclude_unset=True deve distinguere "non passato" da "passato come None"."""

    def test_only_passed_fields_in_dump(self):
        m = UserUpdateRequest.model_validate({"is_active": False})
        dumped = m.model_dump(exclude_unset=True)
        assert dumped == {"is_active": False}
        # email/role/group_id non passati NON devono apparire nel dump.
        assert "email" not in dumped
        assert "role" not in dumped
        assert "group_id" not in dumped

    def test_empty_body_valid_but_yields_empty_dump(self):
        m = UserUpdateRequest.model_validate({})
        assert m.model_dump(exclude_unset=True) == {}
