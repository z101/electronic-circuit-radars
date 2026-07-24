import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import requests


@dataclass
class RequestConfig:
    timeout: int = 30
    delay_min: float = 0.5
    delay_max: float = 1.0
    backoff_factor: float = 2.0
    max_retries: int = 3
    max_consecutive_errors: int = 10
    parallel_workers: int = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


class AdaptiveRateLimiter:
    def __init__(self, config: RequestConfig):
        self.config = config
        self._delay = config.delay_min
        self._consecutive_errors = 0
        self._last_request = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            elapsed = time.time() - self._last_request
            jitter = self._delay * random.uniform(0.8, 1.2)
            if elapsed < jitter:
                time.sleep(jitter - elapsed)
            self._last_request = time.time()

    def report_success(self):
        with self._lock:
            self._consecutive_errors = 0
            self._delay = max(
                self.config.delay_min,
                self._delay * 0.9,
            )

    def report_error(self, status_code: Optional[int] = None):
        with self._lock:
            self._consecutive_errors += 0
            if status_code and status_code in (429, 503):
                self._delay = min(
                    self.config.delay_max,
                    self._delay * self.config.backoff_factor,
                )


def fetch_html(url: str, config: RequestConfig, rate_limiter: AdaptiveRateLimiter) -> str:
    headers = {"User-Agent": config.user_agent}
    rate_limiter.wait()
    for attempt in range(1, config.max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=config.timeout)
            resp.raise_for_status()
            rate_limiter.report_success()
            return resp.text
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            rate_limiter.report_error(status)
            if status and status in (429, 503) and attempt < config.max_retries:
                wait_time = 2 ** attempt * 2
                time.sleep(wait_time)
                continue
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            rate_limiter.report_error()
            if attempt < config.max_retries:
                time.sleep(2 ** attempt)
                continue
            raise


def fetch_all_parallel(
    items: list,
    fetch_fn: Callable,
    config: RequestConfig,
    progress_callback: Optional[Callable] = None,
) -> list:
    rate_limiter = AdaptiveRateLimiter(config)
    semaphore = threading.Semaphore(config.parallel_workers)
    results = []

    def _worker(item):
        with semaphore:
            try:
                result = fetch_fn(item, config, rate_limiter)
                if progress_callback:
                    progress_callback(1)
                return (item, result, None)
            except Exception as e:
                if progress_callback:
                    progress_callback(1)
                return (item, None, str(e))

    with ThreadPoolExecutor(max_workers=config.parallel_workers) as executor:
        futures = {executor.submit(_worker, item): item for item in items}
        for future in as_completed(futures):
            results.append(future.result())

    return results
