import os
import time
import logging
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

API_BASE = "https://dev.hackaday.io/v2"
DEFAULT_LIMIT = 100
MIN_DELAY = 0.1
MAX_DELAY = 10.0


def _get_api_key() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, '..', '..', '.env')
    env_path = os.path.normpath(env_path)
    load_dotenv(env_path)
    key = os.environ.get("HACKADAY_IO_API_KEY", "")
    if not key or key == "your_api_key_here":
        raise ValueError(
            "HACKADAY_IO_API_KEY не найден.\n"
            f"1. Получи ключ на https://dev.hackaday.io\n"
            f"2. Запиши его в {env_path}\n"
            "   HACKADAY_IO_API_KEY=твой_ключ\n"
            "Или задай переменную окружения HACKADAY_IO_API_KEY."
        )
    return key


class ApiClient:
    def __init__(self):
        self.api_key = _get_api_key()
        self.BASE_URL = API_BASE
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._delay = MIN_DELAY
        self._last_request = 0.0

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

    def _request(self, endpoint: str, params: dict = None) -> dict:
        self._rate_limit()
        url = f"{API_BASE}{endpoint}"
        if params is None:
            params = {}
        params["api_key"] = self.api_key

        try:
            resp = self.session.get(url, params=params, timeout=30)
            self._last_request = time.time()

            if resp.status_code == 429:
                self._delay = min(self._delay * 2, MAX_DELAY)
                logger.warning("Rate limited (429), backing off to %.1fs", self._delay)
                time.sleep(self._delay)
                return self._request(endpoint, params)

            if resp.status_code == 401:
                raise PermissionError("Invalid API key. Check HACKADAY_IO_API_KEY.")

            if resp.status_code == 404:
                return None

            resp.raise_for_status()
            self._delay = max(self._delay * 0.9, MIN_DELAY)
            return resp.json()

        except requests.exceptions.ConnectionError as e:
            logger.error("Connection error: %s", e)
            raise
        except requests.exceptions.Timeout:
            logger.warning("Request timed out, retrying...")
            time.sleep(2)
            return self._request(endpoint, params)

    def fetch_projects(self, offset: int = 0, limit: int = DEFAULT_LIMIT) -> list:
        data = self._request("/projects", {
            "offset": str(offset),
            "limit": str(limit),
            "orderBy": "Newest"
        })
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("projects", data.get("data", []))
        return []

    def fetch_project(self, project_id: int) -> dict:
        return self._request(f"/projects/{project_id}")

    def fetch_project_tags(self, project_id: int) -> list:
        data = self._request(f"/projects/{project_id}/tags")
        if isinstance(data, list):
            return data
        return []

    def search(self, query: str, offset: int = 0, limit: int = DEFAULT_LIMIT) -> list:
        data = self._request("/search", {
            "search_term": query,
            "offset": str(offset),
            "limit": str(limit)
        })
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results", data.get("data", []))
        return []