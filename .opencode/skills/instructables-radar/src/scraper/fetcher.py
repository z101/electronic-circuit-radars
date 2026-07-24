import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

DEFAULT_USER_DATA_DIR = Path(__file__).resolve().parent.parent.parent / ".temp" / "playwright_data"


class InstructablesSession:
    """Playwright-based browser session for instructables.com listing page.

    Only needed for fetching the category listing page (JS-rendered React SPA
    with 'Load All' infinite scroll). Individual project pages are fetched
    via ProjectFetcher (plain HTTP + JSON-LD).
    """

    def __init__(self, headless: bool = False, user_data_dir: str | None = None):
        self.headless = headless
        self.user_data_dir = str(user_data_dir or DEFAULT_USER_DATA_DIR)
        self._playwright = None
        self._context = None
        self._page = None
        self._cookies_accepted = False

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.new_page()
        return self

    def __exit__(self, *args):
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def fetch_html(self, url: str, timeout: int = 60000) -> str:
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        time.sleep(3)

        if not self._cookies_accepted:
            self._accept_cookies()

        self._click_load_all()

        self._page.wait_for_load_state("load", timeout=10_000)
        return self._page.content()

    def _accept_cookies(self):
        try:
            accept = self._page.query_selector('button:has-text("Accept all")')
            if accept:
                accept.click()
                time.sleep(1)
                self._cookies_accepted = True
                logger.info("Cookie consent accepted")
        except Exception:
            pass

    def _click_load_all(self):
        max_clicks = 500
        for i in range(max_clicks):
            try:
                load_btn = self._page.query_selector('button:has-text("Load All")')
                if not load_btn:
                    logger.info("No more 'Load All' buttons, %d clicks done", i)
                    break
                load_btn.click()
                time.sleep(0.5)
                logger.info("Clicked 'Load All' (%d/%d)", i + 1, max_clicks)
            except Exception:
                break

    def close(self):
        self.__exit__(None, None, None)


class ProjectFetcher:
    """Lightweight parallel HTTP fetcher for individual project pages.

    Uses requests + ThreadPoolExecutor. Pages contain JSON-LD structured data
    — no browser needed.
    """

    def __init__(self, workers: int = 50, delay: float = 0.2):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        self.workers = workers
        self.delay = delay

    def fetch_many(self, articles: list[dict]) -> list[dict]:
        """Fetch many project pages in parallel.

        Input: [{"id": 1, "url": "..."}, ...]
        Output: [{"id": 1, "html": "..."}, ...]

        Failed fetches have html=None.
        """
        results = [None] * len(articles)
        lock = __import__("threading").Lock()
        done = [0]

        def _fetch(i: int, url: str):
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                with lock:
                    results[i] = {"id": articles[i]["id"], "html": resp.text}
                    done[0] += 1
            except Exception as e:
                logger.warning("Failed [%d] %s: %s", articles[i]["id"], url, e)
                with lock:
                    results[i] = {"id": articles[i]["id"], "html": None}
                    done[0] += 1

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for i, a in enumerate(articles):
                ex.submit(_fetch, i, a["url"])

        return results