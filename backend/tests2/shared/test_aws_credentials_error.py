"""
Unit test per `app.infrastructure.storage.cloud_backends.aws_credentials_error_message`.

Helper boundary che riconosce errori boto3/botocore di credenziali AWS
"""

import pytest

from app.infrastructure.storage.cloud_backends import aws_credentials_error_message


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_recognizes_login_refresh_required():
    from botocore.exceptions import LoginRefreshRequired

    msg = aws_credentials_error_message(LoginRefreshRequired())
    assert msg is not None
    assert "AWS credentials expired" in msg
    assert "aws sso login" in msg


def test_recognizes_no_credentials_error():
    from botocore.exceptions import NoCredentialsError

    msg = aws_credentials_error_message(NoCredentialsError())
    assert msg is not None
    assert "AWS credentials" in msg


def test_recognizes_token_retrieval_error():
    from botocore.exceptions import TokenRetrievalError

    exc = TokenRetrievalError(provider="sso", error_msg="boom")
    msg = aws_credentials_error_message(exc)
    assert msg is not None
    assert "AWS credentials" in msg


def test_recognizes_access_denied_with_refresh_token_message():
    """Caso reale dalla telemetria: ClientError AccessDeniedException con
    Message che contiene 'refresh token has expired' / 'CreateOAuth2Token'.
    """
    from botocore.exceptions import ClientError

    error_response = {
        "Error": {
            "Code": "AccessDeniedException",
            "Message": (
                "CreateOAuth2Token: The refresh token has expired"
            ),
        }
    }
    exc = ClientError(error_response, "CreateOAuth2Token")

    msg = aws_credentials_error_message(exc)
    assert msg is not None
    assert "AWS credentials" in msg


def test_does_not_misclassify_generic_access_denied():
    """Un AccessDenied 'vero' (es. policy bucket sbagliata, niente refresh
    token nel messaggio) NON deve essere mappato come credenziali scadute,
    altrimenti l'operatore segue una pista sbagliata.
    """
    from botocore.exceptions import ClientError

    error_response = {
        "Error": {
            "Code": "AccessDenied",
            "Message": "User: arn:aws:iam::123:user/x is not authorized to PutObject",
        }
    }
    exc = ClientError(error_response, "PutObject")

    assert aws_credentials_error_message(exc) is None


def test_does_not_misclassify_generic_exception():
    """Errori non-AWS (network, ValueError, ecc.) ritornano None: il caller
    continua a propagarli come errore generico.
    """
    assert aws_credentials_error_message(RuntimeError("network down")) is None
    assert aws_credentials_error_message(ValueError("bad input")) is None
    assert aws_credentials_error_message(KeyError("foo")) is None
