from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from scripts.provision_local_s3_buckets import ensure_bucket


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "simulated"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "HeadBucket",
    )


def test_ensure_bucket_does_not_recreate_existing_bucket():
    client = MagicMock()

    assert ensure_bucket(client, "plenora-runtime") is False

    client.head_bucket.assert_called_once_with(Bucket="plenora-runtime")
    client.create_bucket.assert_not_called()


def test_ensure_bucket_creates_missing_bucket():
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("NoSuchBucket", 404)

    assert ensure_bucket(client, "plenora-backups") is True

    client.create_bucket.assert_called_once_with(Bucket="plenora-backups")


def test_ensure_bucket_does_not_hide_access_denied():
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("AccessDenied", 403)

    try:
        ensure_bucket(client, "plenora-backups")
    except ClientError as exc:
        assert exc.response["Error"]["Code"] == "AccessDenied"
    else:
        raise AssertionError("AccessDenied non deve essere trattato come bucket assente")

    client.create_bucket.assert_not_called()
