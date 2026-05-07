"""
Lock tests per `tests2.functional.harness.http._try_parse_json_body`.

Regression guard: l'helper esplodeva con `json.JSONDecodeError` /
"""

import pytest

from tests2.functional.harness.http import _try_parse_json_body


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class TestTryParseJsonBody:
    def test_empty_bytes_returns_none(self):
        assert _try_parse_json_body(b"") is None

    def test_valid_json_object_parsed_to_dict(self):
        result = _try_parse_json_body(b'{"key": "value", "n": 1}')
        assert result == {"key": "value", "n": 1}

    def test_valid_json_array_parsed_to_list(self):
        assert _try_parse_json_body(b"[1, 2, 3]") == [1, 2, 3]

    def test_csv_text_returns_string_not_raises(self):
        """Body CSV (text/plain) — non e' JSON, non deve sollevare."""
        body = b"id,name\n1,foo\n2,bar\n"
        result = _try_parse_json_body(body)
        assert isinstance(result, str)
        assert "id,name" in result

    def test_html_text_returns_string_not_raises(self):
        body = b"<html><body>Error 500</body></html>"
        result = _try_parse_json_body(body)
        assert isinstance(result, str)
        assert "<html>" in result

    def test_binary_payload_returns_bytes_not_raises(self):
        """ZIP/parquet/binary non decodabili come utf-8 → bytes raw."""
        # Bytes che non sono utf-8 valido (ad es. ZIP magic header con bytes alti)
        body = bytes([0x50, 0x4B, 0x03, 0x04, 0xFF, 0xFE, 0xFD, 0x80])
        result = _try_parse_json_body(body)
        assert isinstance(result, bytes)
        assert result == body

    def test_malformed_json_string_returns_string_not_raises(self):
        """JSON-looking ma malformato → stringa, non eccezione."""
        body = b'{"unterminated": '
        result = _try_parse_json_body(body)
        assert isinstance(result, str)
        assert "unterminated" in result
