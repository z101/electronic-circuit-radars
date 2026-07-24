import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestApiKey:
    def test_api_key_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("HACKADAY_IO_API_KEY", "test_key_123")
        from scraper.api_client import _get_api_key, ApiClient
        key = _get_api_key()
        assert key == "test_key_123"
        client = ApiClient()
        assert client.api_key == "test_key_123"